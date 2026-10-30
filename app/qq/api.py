"""QQ 官方机器人 OpenAPI 客户端（纯 stdlib，无第三方依赖）。

认证流程：
  1. POST https://bots.qq.com/app/getAppAccessToken  (appId + clientSecret)
     -> access_token（有效期约 7200 秒，提前 60 秒续期）
  2. 之后所有业务请求带 header: Authorization: QQBot <access_token>

消息形态（本模块覆盖的）：
  · C2C（单聊）   POST /v2/users/{openid}/messages
  · 群聊 @        POST /v2/groups/{group_openid}/messages
  · 语音上传      POST /v2/users/{openid}/files  (file_type=3)
  · 发语音        msg_type=7 + media.file_info

被动回复（带上触发消息的 msg_id）不需要额外主动消息额度，
这也是我们唯一会用的方式：用户说一句、我们回一句。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
API_BASE = "https://api.sgroup.qq.com"

# intents 位
INTENT_GROUP_AND_C2C_EVENT = 1 << 25
INTENT_PUBLIC_GUILD_MESSAGES = 1 << 30


class QQBotError(RuntimeError):
    """QQ 开放平台返回错误。"""


class QQApi:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        api_base: str = API_BASE,
        token_url: str = TOKEN_URL,
        timeout: float = 30.0,
    ):
        if not app_id or not app_secret:
            raise QQBotError("缺少 AppID / AppSecret")
        self.app_id = str(app_id).strip()
        self.app_secret = str(app_secret).strip()
        self.api_base = api_base.rstrip("/")
        self.token_url = token_url
        self.timeout = timeout
        self._token = ""
        self._expires_at = 0.0

    # ---------------- 认证 ----------------
    def access_token(self, *, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and now < self._expires_at - 60:
            return self._token
        body = json.dumps(
            {"appId": self.app_id, "clientSecret": self.app_secret}
        ).encode("utf-8")
        req = urllib.request.Request(
            self.token_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise QQBotError(f"取 access_token 失败 HTTP {exc.code}: {detail}") from exc
        data = json.loads(raw.decode("utf-8"))
        token = data.get("access_token")
        if not token:
            raise QQBotError(f"取 access_token 失败：{data}")
        self._token = token
        expires = data.get("expires_in", 7200)
        try:
            self._expires_at = now + float(expires)
        except (TypeError, ValueError):
            self._expires_at = now + 7200.0
        log.debug("access_token 已刷新，%s 秒后过期", int(self._expires_at - now))
        return token

    # ---------------- 通用请求 ----------------
    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = path if path.startswith("http") else self.api_base + path
        headers = {
            "Authorization": f"QQBot {self.access_token()}",
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise QQBotError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {"raw": raw.decode("utf-8", "replace")}

    # ---------------- 网关 ----------------
    def gateway_url(self) -> str:
        data = self._request("GET", "/gateway")
        url = data.get("url") or ""
        if not url:
            raise QQBotError(f"取网关地址失败：{data}")
        return url

    # ---------------- 发消息 ----------------
    def send_c2c_text(self, openid: str, content: str, msg_id: str, *, msg_seq: int = 1) -> dict:
        return self._request(
            "POST",
            f"/v2/users/{openid}/messages",
            {"content": content, "msg_type": 0, "msg_id": msg_id, "msg_seq": msg_seq},
        )

    def send_group_text(self, group_openid: str, content: str, msg_id: str, *, msg_seq: int = 1) -> dict:
        return self._request(
            "POST",
            f"/v2/groups/{group_openid}/messages",
            {"content": content, "msg_type": 0, "msg_id": msg_id, "msg_seq": msg_seq},
        )

    # ---------------- 上传文件 / 发语音 ----------------
    def upload_c2c_file(
        self,
        openid: str,
        *,
        file_type: int = 3,
        file_data: bytes | None = None,
        url: str | None = None,
        srv_send_msg: bool = False,
        file_name: str | None = None,
    ) -> dict:
        """上传富媒体到 C2C 会话。

        file_type: 1=图片 2=视频 3=语音 4=文件
        返回里含 file_info，把它放进 msg_type=7 的 media 里发送。
        """
        import base64

        payload: dict = {"file_type": file_type, "srv_send_msg": srv_send_msg}
        if file_data is not None:
            payload["file_data"] = base64.b64encode(file_data).decode("ascii")
        elif url is not None:
            payload["url"] = url
        else:
            raise QQBotError("upload_c2c_file 需要 file_data 或 url 之一")
        if file_name:
            payload["file_name"] = file_name
        return self._request("POST", f"/v2/users/{openid}/files", payload)

    def upload_group_file(
        self,
        group_openid: str,
        *,
        file_type: int = 3,
        file_data: bytes | None = None,
        url: str | None = None,
        srv_send_msg: bool = False,
        file_name: str | None = None,
    ) -> dict:
        import base64

        payload: dict = {"file_type": file_type, "srv_send_msg": srv_send_msg}
        if file_data is not None:
            payload["file_data"] = base64.b64encode(file_data).decode("ascii")
        elif url is not None:
            payload["url"] = url
        else:
            raise QQBotError("upload_group_file 需要 file_data 或 url 之一")
        if file_name:
            payload["file_name"] = file_name
        return self._request("POST", f"/v2/groups/{group_openid}/files", payload)

    def send_c2c_media(self, openid: str, file_info: str, msg_id: str, *, msg_seq: int = 2) -> dict:
        """发富媒体（msg_type=7）。语音和表情包走同一个接口，只是 file_info 来源不同。"""
        return self._request(
            "POST",
            f"/v2/users/{openid}/messages",
            {
                "content": "",
                "msg_type": 7,
                "msg_id": msg_id,
                "msg_seq": msg_seq,
                "media": {"file_info": file_info},
            },
        )

    def send_group_media(self, group_openid: str, file_info: str, msg_id: str, *, msg_seq: int = 2) -> dict:
        return self._request(
            "POST",
            f"/v2/groups/{group_openid}/messages",
            {
                "content": "",
                "msg_type": 7,
                "msg_id": msg_id,
                "msg_seq": msg_seq,
                "media": {"file_info": file_info},
            },
        )

    # 语音就是富媒体的一种，保留语义化的别名，调用处读起来更清楚
    def send_c2c_voice(self, openid: str, file_info: str, msg_id: str, *, msg_seq: int = 2) -> dict:
        return self.send_c2c_media(openid, file_info, msg_id, msg_seq=msg_seq)

    def send_group_voice(self, group_openid: str, file_info: str, msg_id: str, *, msg_seq: int = 2) -> dict:
        return self.send_group_media(group_openid, file_info, msg_id, msg_seq=msg_seq)
