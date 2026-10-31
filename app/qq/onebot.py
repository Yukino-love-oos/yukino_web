"""OneBot 11（NapCat）客户端：个人号路线。

和官方机器人的区别
------------------
| | 官方机器人 | NapCat / OneBot |
|---|---|---|
| 身份 | 官方 bot AppID | **真实 QQ 小号** |
| 收发 | WebSocket 网关 + OpenAPI | 事件推送（WS）+ HTTP 调 API |
| 被动回复限制 | 有（msg_id + msg_seq） | **没有** |
| 主动消息 | 有额度限制 | 想发就发 |
| 合规 | 合规 | ⚠ 违反 QQ 用户协议，有封号风险 |

NapCat 一般开两个端口：
  · HTTP  API     —— 我们调 send_private_msg 等
  · WS    事件流  —— 我们收 message 事件

纯 stdlib：HTTP 用 urllib，WS 复用 gateway.py 里自己实现的 WebSocket 客户端。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class OneBotError(RuntimeError):
    pass


# ---------------------------------------------------------------- HTTP 客户端
class OneBotClient:
    def __init__(self, http_base: str = "http://127.0.0.1:3000", *, token: str = "",
                 timeout: float = 30.0):
        self.base = (http_base or "").rstrip("/")
        self.token = (token or "").strip()
        self.timeout = timeout

    def call(self, action: str, payload: dict | None = None) -> dict:
        url = "%s/%s" % (self.base, action.lstrip("/"))
        data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            # OneBot 11 两种鉴权写法都支持
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise OneBotError("%s -> HTTP %s: %s" % (action, exc.code, detail)) from exc
        except urllib.error.URLError as exc:
            raise OneBotError("%s 连接失败（NapCat 没开？）：%r" % (action, exc)) from exc
        try:
            out = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            return {"raw": raw.decode("utf-8", "replace")}
        if isinstance(out, dict) and out.get("status") == "failed":
            raise OneBotError("%s 失败：%s" % (action, out))
        return out

    # ---------------- 常用动作 ----------------
    def get_login_info(self) -> dict:
        return (self.call("get_login_info") or {}).get("data") or {}

    def send_private_msg(self, user_id, segments: list[dict]) -> dict:
        return self.call("send_private_msg", {"user_id": int(user_id), "message": segments})

    def send_group_msg(self, group_id, segments: list[dict]) -> dict:
        return self.call("send_group_msg", {"group_id": int(group_id), "message": segments})


# ---------------------------------------------------------------- 事件解析
def extract_text(message) -> str:
    """从 OneBot message（段数组或 CQ 码字符串）里取出纯文本。"""
    if isinstance(message, str):
        # 不带 CQ 码的简单情况直接把 [CQ:...] 去掉
        import re

        return re.sub(r"\[CQ:[^\]]+\]", "", message).strip()
    parts: list[str] = []
    for seg in message or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("type") == "text":
            parts.append(str((seg.get("data") or {}).get("text") or ""))
    return "".join(parts).strip()


def is_at_me(message, self_id) -> bool:
    """群里是否 @ 了我。"""
    for seg in message or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("type") != "at":
            continue
        qq = str((seg.get("data") or {}).get("qq") or "")
        if qq and (qq == str(self_id) or qq == "all"):
            return qq == str(self_id)
    return False


def parse_event(raw: dict | str, *, self_id=None) -> dict | None:
    """把 OneBot 事件解析成统一格式；不需要处理的返回 None。

    统一格式：{"kind": "c2c"|"group", "target": str, "message_id": str,
              "text": str, "sender": str, "self_id": str}
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None

    # 兼容两类上报：OneBot 11 的 post_type 风格、以及部分实现的 message 风格
    post_type = raw.get("post_type") or raw.get("type")
    if post_type not in ("message", "message_private", "message_group"):
        return None

    sid = str(raw.get("self_id") or self_id or "")
    sender_id = str(raw.get("user_id") or (raw.get("sender") or {}).get("user_id") or "")
    if sid and sender_id and sid == sender_id:
        return None                       # 自己发的消息，别自问自答

    message = raw.get("message")
    text = extract_text(message) if message is not None else str(raw.get("raw_message") or "").strip()
    if not text:
        return None                       # 图片/表情等非文本，暂不处理

    mid = str(raw.get("message_id") or "")
    message_type = raw.get("message_type") or ("group" if post_type == "message_group" else "private")

    if message_type == "group":
        if not is_at_me(message, sid):
            return None                   # 群里只在被 @ 时回应
        return {
            "kind": "group",
            "target": str(raw.get("group_id") or ""),
            "message_id": mid,
            "text": text,
            "sender": sender_id,
            "self_id": sid,
        }
    return {
        "kind": "c2c",
        "target": sender_id,
        "message_id": mid,
        "text": text,
        "sender": sender_id,
        "self_id": sid,
    }


# ---------------------------------------------------------------- 事件推送
class OneBotRunner:
    """连 NapCat 的 WS 事件流，收到消息就交给 on_message。"""

    def __init__(self, client: OneBotClient, ws_url: str, *,
                 token: str = "", on_message=None, on_log=None):
        self.client = client
        self.ws_url = ws_url
        self.token = (token or "").strip()
        self.on_message = on_message or (lambda ev: None)
        self.on_log = on_log or (lambda msg: log.info("%s", msg))
        self.self_id = ""

    def _url(self) -> str:
        url = self.ws_url
        if self.token and "access_token=" not in url:
            sep = "&" if "?" in url else "?"
            url += "%saccess_token=%s" % (sep, self.token)
        return url

    def _resolve_self_id(self) -> str:
        try:
            info = self.client.get_login_info()
            sid = str(info.get("user_id") or "")
            if sid:
                log.info("OneBot 登录账号：%s (%s)", info.get("nickname"), sid)
            return sid
        except OneBotError as exc:
            self.on_log("取登录信息失败（不影响收消息）：%s" % exc)
            return ""

    def run_once(self) -> None:
        from .gateway import WebSocket

        self.self_id = self.self_id or self._resolve_self_id()
        ws = WebSocket(self._url(), timeout=60)
        ws.connect()
        self.on_log("已连上 NapCat 事件流：%s" % self.ws_url)
        while True:
            raw = ws.recv_text()
            if raw is None:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            ev = parse_event(data, self_id=self.self_id)
            if not ev:
                continue
            if not self.self_id and ev.get("self_id"):
                self.self_id = ev["self_id"]
            try:
                self.on_message(ev)
            except Exception:  # noqa: BLE001
                log.exception("处理 OneBot 消息失败")

    def run_forever(self, *, max_backoff: float = 30.0) -> None:
        backoff = 1.0
        while True:
            try:
                self.run_once()
                backoff = 1.0
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                self.on_log("NapCat 连接异常：%r，%.0fs 后重连" % (exc, backoff))
                time.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)
