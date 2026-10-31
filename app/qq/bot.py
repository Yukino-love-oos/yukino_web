"""雪之下雪乃 · QQ 官方机器人适配器。

设计要点
--------
1. **长连接**：QQ 用 WebSocket 主动连出去（`api.sgroup.qq.com`），
   所以服务器不需要公网回调地址、不需要域名、不需要 ICP 备案。
   只要服务器能出网就行 —— 这正是我们这台 ECS 的情况。

2. **先文字、后语音**：TTS 在 CPU 上是 RT 6~7 倍，一段 5 秒语音要合成半分钟。
   如果等 TTS 完再回消息，用户要干等一分钟。所以：
     · 先用 `respond(speak=False)` 只拿文本（约 2~5 秒）→ 立刻回文字
     · 再逐句 `speak()` 合成 → 转 SILK → 补发语音消息
   文字是先到的，语音后补，体验上「秒回 + 语音跟上」。

3. **不重复回复**：QQ 断线重连可能重推事件，用 message id 做幂等去重。

4. **单用户串行**：一台 ECS 同时只跑一个 TTS 进程，并发合并没有意义，
   所以消息进队列，一个 worker 顺序处理。

用法
----
    python -m app.qq                  # 正式运行
    python -m app.qq --dry-run        # 只验证凭据+网关连通，不说话

需要的环境变量（或 .env）：
    QQ_APP_ID        机器人 AppID
    QQ_APP_SECRET    机器人 AppSecret
    QQ_VOICE         1/0，是否补发语音（默认 1）
    QQ_MAX_VOICE     每条消息最多几条语音（默认 2，QQ 被动回复有条数上限）
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import quote

from ..config import load_settings
from ..pipeline import build_pipeline
from ..state import expression
from ..tts.cache import merge_wav
from ..tts.silence import squeeze_pauses
from . import onebot, silk, transport
from .api import INTENT_GROUP_AND_C2C_EVENT, QQApi, QQBotError
from .gateway import QQGateway

log = logging.getLogger(__name__)

T_C2C = "C2C_MESSAGE_CREATE"
T_GROUP_AT = "GROUP_AT_MESSAGE_CREATE"

# 表情包目录（由 app/ui/assets/expressions 的立绘裁切而来）
STICKER_DIR = Path(__file__).resolve().parents[1] / "ui" / "assets" / "stickers"


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        # 也看看 .env
        try:
            from ..config import ROOT, parse_dotenv

            vals = parse_dotenv(ROOT / ".env")
            raw = vals.get(name)
        except Exception:  # noqa: BLE001
            raw = None
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw:
        return raw.strip()
    try:
        from ..config import ROOT, parse_dotenv

        return (parse_dotenv(ROOT / ".env").get(name) or default).strip()
    except Exception:  # noqa: BLE001
        return default


class QQBot:
    def __init__(self, settings=None, *, dry_run: bool = False):
        # 后端：official=官方机器人（合规，被动回复）| onebot=NapCat 个人号
        self.backend = (_env_str("QQ_BACKEND", "official") or "official").strip().lower()
        self.app_id = _env_str("QQ_APP_ID")
        self.app_secret = _env_str("QQ_APP_SECRET")
        self.api = None
        self.onebot_client = None
        self.transport = None

        if self.backend != "onebot":
            if not self.app_id or not self.app_secret:
                raise SystemExit(
                    "缺少 QQ_APP_ID / QQ_APP_SECRET。\n"
                    "在 .env 里加上：\n"
                    "  QQ_APP_ID=你的AppID\n"
                    "  QQ_APP_SECRET=你的AppSecret\n"
                    "（若走 NapCat 个人号，请设 QQ_BACKEND=onebot）"
                )
            self.api = QQApi(self.app_id, self.app_secret)
        self.dry_run = dry_run
        self.voice_enabled = _env_bool("QQ_VOICE", True)
        self.max_voice = int(_env_str("QQ_MAX_VOICE", "2") or "2")
        # 短语间保留多长的停顿；实测 180ms 听感最自然（80/120/180 三档对比）
        self.pause_ms = int(_env_str("QQ_PAUSE_MS", "180") or "180")
        # 分条发送：回复最多拆成几条、条间隔多少毫秒（模仿真人一句一句发）
        self.text_parts = int(_env_str("QQ_TEXT_PARTS", "3") or "3")
        self.text_gap = float(_env_str("QQ_TEXT_GAP", "0.6") or "0.6")
        # 表情包开关
        self.sticker_enabled = _env_bool("QQ_STICKER", True)
        # 输出模式：random（按心情掷骰）| voice（总是语音）| text（总是文字）
        self.output_mode = (_env_str("QQ_OUTPUT_MODE", "random") or "random").strip().lower()

        if self.backend == "onebot":
            self.onebot_client = onebot.OneBotClient(
                _env_str("ONEBOT_HTTP", "http://127.0.0.1:3000"),
                token=_env_str("ONEBOT_TOKEN", ""),
            )
            self.transport = transport.OneBotTransport(self.onebot_client)
            log.info("后端 = NapCat / OneBot（个人号）")
        else:
            self.transport = transport.OfficialTransport(
                self.api,
                media_base=_env_str("QQ_MEDIA_BASE", ""),
                media_token=_env_str("YUKINO_TOKEN", ""),
            )
            log.info("后端 = QQ 官方机器人")
        self.settings = settings or load_settings()
        self.pipeline = None
        if not dry_run:
            self.pipeline = build_pipeline(self.settings)
        self._seen: dict[str, float] = {}
        self._seen_lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue(maxsize=32)
        self._worker: threading.Thread | None = None

    # ---------------- 去重 ----------------
    def _first_time(self, mid: str) -> bool:
        if not mid:
            return True
        now = time.time()
        with self._seen_lock:
            # 顺手清理 10 分钟前的记录
            if len(self._seen) > 512:
                for k, ts in list(self._seen.items()):
                    if now - ts > 600:
                        del self._seen[k]
            if mid in self._seen:
                return False
            self._seen[mid] = now
            return True

    # ---------------- 事件入口 ----------------
    def on_event(self, t: str, d: dict) -> None:
        if t not in (T_C2C, T_GROUP_AT):
            return
        mid = str(d.get("id") or "")
        content = (d.get("content") or "").strip()
        author = d.get("author") or {}
        if t == T_C2C:
            target = str(author.get("user_openid") or "")
            kind = "c2c"
        else:
            target = str(d.get("group_openid") or "")
            kind = "group"
        if not mid or not target:
            log.warning("事件缺字段，忽略：t=%s", t)
            return
        if not content:
            log.info("空消息，忽略（可能是图片/表情）")
            return
        if not self._first_time(mid):
            log.debug("重复事件，忽略：%s", mid)
            return
        log.info("[%s] 收到：%s", kind, content[:80])
        try:
            self._queue.put_nowait((kind, target, mid, content))
        except queue.Full:
            log.warning("队列已满，丢弃消息 %s", mid)

    # ---------------- 工作线程 ----------------
    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            kind, target, mid, content = item
            try:
                self._handle(kind, target, mid, content)
            except Exception:  # noqa: BLE001
                log.exception("处理消息失败")
            finally:
                self._queue.task_done()

    def start_worker(self) -> None:
        self._worker = threading.Thread(target=self._worker_loop, name="qq-worker", daemon=True)
        self._worker.start()

    # ---------------- 发送封装 ----------------
    def _send_text(self, kind: str, target: str, mid: str, text: str, seq: int = 1) -> None:
        if not text.strip():
            return
        self.transport.send_text(kind, target, mid, text, seq=seq)

    def _split_reply(self, text: str) -> list[str]:
        """把一段回复拆成 2~3 条短消息，模仿真人分条发。

        雪乃说话本来就短促（「ふん。」「……そう。」「別に。」），一次甩一大段
        不像她。按句末标点和换行切，短段合并、超长段独占一条。
        """
        text = (text or "").strip()
        if not text:
            return []
        limit = max(1, self.text_parts)
        # 先按换行/句末标点切
        pieces = re.split(r"[\n。！？!?]+", text)
        pieces = [p.strip(" \t　、,，") for p in pieces]
        pieces = [p for p in pieces if p]
        if not pieces:
            return [text]
        if len(pieces) <= limit:
            out = pieces
        else:
            # 合并到不超过 limit 段，尽量均分
            out, cur = [], ""
            per = math.ceil(len(pieces) / limit)
            for p in pieces:
                cur = (cur + "。" + p) if cur else p
                if len([x for x in cur.split("。") if x]) >= per:
                    out.append(cur)
                    cur = ""
            if cur:
                out.append(cur)
            out = out[:limit]
        # 单段太短的补上句号，读起来完整
        return [p if p[-1] in "。！？!?…" else p + "。" for p in out]

    def _send_sticker(self, kind: str, target: str, mid: str, name: str, seq: int) -> bool:
        """发一张表情包（官方通道走 file_type=1，NapCat 走 image 段）。"""
        path = STICKER_DIR / ("%s.png" % name)
        try:
            data = path.read_bytes()
        except OSError:
            log.warning("表情包文件不存在：%s", path.name)
            return False
        return self.transport.send_image(kind, target, mid, data, seq=seq)

    def _publish_silk(self, data: bytes) -> str | None:
        """把 SILK 落到 web 服务的 data/qq_media 下，返回公网 URL。

        为什么需要这一步：QQ 的富媒体上传接口对 base64(file_data) 送语音
        会返回 850019「富媒体文件格式不支持」，必须给一个腾讯服务器能拉取的
        `url`。我们本来就有公网 web 服务（8501），顺手当临时文件服务器用。
        """
        base = _env_str("QQ_MEDIA_BASE")
        if not base:
            return None
        token = _env_str("YUKINO_TOKEN")
        media_dir = Path(__file__).resolve().parents[2] / "data" / "qq_media"
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
            name = "y%s.silk" % uuid.uuid4().hex
            (media_dir / name).write_bytes(data)
            # 只留最近 16 个，避免长期堆积
            try:
                olds = sorted(media_dir.glob("*.silk"), key=lambda p: p.stat().st_mtime)
                for old in olds[:-16]:
                    old.unlink()
            except OSError:
                pass
        except OSError as exc:
            log.warning("写临时 SILK 失败：%r", exc)
            return None
        url = "%s/media/%s" % (base.rstrip("/"), name)
        if token:
            url += "?t=" + quote(token, safe="")
        return url

    def _upload_voice(self, kind: str, target: str, data: bytes) -> dict:
        """先走 base64(file_data)（实测可通），失败再退回公网 url。"""
        try:
            if kind == "c2c":
                return self.api.upload_c2c_file(
                    target, file_type=3, file_data=data, file_name="yukino.silk"
                )
            return self.api.upload_group_file(
                target, file_type=3, file_data=data, file_name="yukino.silk"
            )
        except QQBotError as exc:
            log.warning("base64 上传语音失败，改用公网 url：%s", exc)
        url = self._publish_silk(data)
        if not url:
            raise QQBotError("base64 与 url 两种上传方式都不可用")
        if kind == "c2c":
            return self.api.upload_c2c_file(target, file_type=3, url=url)
        return self.api.upload_group_file(target, file_type=3, url=url)

    def _send_voice(self, kind: str, target: str, mid: str, wav: bytes, seq: int) -> bool:
        # 先压掉合成时插入的长停顿（52% 静音 -> 30% 左右，见 app/tts/silence.py）
        try:
            compressed = squeeze_pauses(wav, max_gap_ms=self.pause_ms)
            if compressed:
                wav = compressed
        except Exception as exc:  # noqa: BLE001
            log.debug("停顿压缩失败，用原始音频：%r", exc)
        data = silk.encode_wav_to_silk(wav)
        if not data:
            return False
        return self.transport.send_silk(kind, target, mid, data, seq=seq)

    def _synth_full(self, turn) -> bytes | None:
        """把这一轮所有台词合成成**一整段**音频。

        为什么不分句发：按句拆成好几条语音，听感是一顿一顿的
        （之前实测过，静音占比 50%+）。整段连着说才像人在说话。
        """
        sentences = [x for x in getattr(turn, "sentences", []) if x and x.strip()]
        if not sentences:
            return None
        chunks: list[bytes] = []
        for sentence in sentences:
            try:
                wav = self.pipeline.speak(sentence)
            except Exception as exc:  # noqa: BLE001
                log.warning("合成失败：%r", exc)
                continue
            if wav:
                chunks.append(wav)
        if not chunks:
            return None
        if len(chunks) == 1:
            return chunks[0]
        return merge_wav(chunks)

    def _send_full_voice(self, kind: str, target: str, mid: str, turn, seq: int) -> bool:
        if not self.voice_enabled:
            return False
        if not silk.available_backends():
            log.info("没有 SILK 编码器，跳过语音（改发文字）")
            return False
        wav = self._synth_full(turn)
        if not wav:
            return False
        ok = self._send_voice(kind, target, mid, wav, seq)
        if ok:
            log.info("整段语音已发（%.1f 秒）", len(wav) / 32000.0 / 2.0)
        return ok

    def _handle(self, kind: str, target: str, mid: str, content: str) -> None:
        if self.pipeline is None:
            self._send_text(kind, target, mid, "（服务未就绪）")
            return

        t0 = time.time()

        # ---- 第一段：只出文字（跳过 TTS），先把内容拿到手 ----
        turn = None
        for ev in self.pipeline.respond(content, speak=False):
            if ev.get("type") == "done":
                turn = ev.get("turn")
            elif ev.get("type") == "error":
                log.warning("生成失败：%s", ev.get("message"))
        if turn is None:
            self._send_text(kind, target, mid, "……（有点说不出来）")
            return

        # ---- 心情 → 输出模式（语音 or 文字，加权随机）----
        try:
            self.pipeline.session.set_mood(turn.expression)
        except Exception:  # noqa: BLE001
            pass
        mood_state = self.pipeline.session.current_mood()
        force = None if self.output_mode not in ("voice", "text") else self.output_mode
        decision = expression.choose_mode(
            mood_state, self.pipeline.session.affinity, force=force
        )
        log.info("输出模式 → %s（%s）", decision["mode"], decision["reason"])

        reply = (turn.zh or "").strip() or (turn.ja or "").strip()
        sticker = (getattr(turn, "sticker", "") or "").strip()
        seq = 1

        if decision["mode"] == "voice":
            # 语音先发，**念完再输出文字**
            if self._send_full_voice(kind, target, mid, turn, seq):
                seq += 1
            try:
                self._send_text(kind, target, mid, reply, seq=seq)
                seq += 1
            except QQBotError as exc:
                log.warning("回文字失败：%s", exc)
                return
            if sticker and self.sticker_enabled:
                if self._send_sticker(kind, target, mid, sticker, seq):
                    seq += 1
        else:
            # 文字模式：一条消息发完，不做短句拆分
            try:
                self._send_text(kind, target, mid, reply, seq=seq)
                seq += 1
            except QQBotError as exc:
                log.warning("回文字失败：%s", exc)
                return
            if sticker and self.sticker_enabled:
                if self._send_sticker(kind, target, mid, sticker, seq):
                    seq += 1

        log.info("本轮完成：模式=%s，用时 %.1fs", decision["mode"], time.time() - t0)

    # ---------------- 入口 ----------------
    def on_onebot_message(self, ev: dict) -> None:
        """NapCat 事件入口（和官方网关事件走同一套队列）。"""
        mid = str(ev.get("message_id") or "")
        text = (ev.get("text") or "").strip()
        kind = ev.get("kind") or "c2c"
        target = str(ev.get("target") or "")
        if not text or not target:
            return
        if not self._first_time(mid):
            log.debug("重复事件，忽略：%s", mid)
            return
        log.info("[%s] 收到：%s", kind, text[:80])
        try:
            self._queue.put_nowait((kind, target, mid, text))
        except queue.Full:
            log.warning("队列已满，丢弃消息 %s", mid)

    def run(self) -> None:
        if self.dry_run:
            log.info("dry-run：验证后端连通性…")
            if self.backend == "onebot":
                info = self.onebot_client.get_login_info()
                log.info("NapCat 登录账号：%s (%s)", info.get("nickname"), info.get("user_id"))
                from .onebot import OneBotRunner
                from .gateway import WebSocket

                runner = OneBotRunner(self.onebot_client, _env_str("ONEBOT_WS", "ws://127.0.0.1:3001"))
                ws = WebSocket(runner._url(), timeout=30)
                ws.connect()
                log.info("NapCat 事件流握手成功")
                ws.close()
                log.info("dry-run 通过：NapCat HTTP + WS 都通。")
            else:
                token = self.api.access_token()
                log.info("access_token 前 12 位：%s…", token[:12])
                url = self.api.gateway_url()
                log.info("网关地址：%s", url)
                gw = QQGateway(self.api, intents=INTENT_GROUP_AND_C2C_EVENT, on_event=lambda t, d: None)
                gw.connect()
                log.info("网关 WebSocket 握手成功")
                gw.close()
                log.info("dry-run 通过：凭据有效、网关可达。")
            return

        self.start_worker()

        if self.backend == "onebot":
            from .onebot import OneBotRunner

            runner = OneBotRunner(
                self.onebot_client,
                _env_str("ONEBOT_WS", "ws://127.0.0.1:3001"),
                token=_env_str("ONEBOT_TOKEN", ""),
                on_message=self.on_onebot_message,
                on_log=lambda m: log.info("%s", m),
            )
            log.info("启动 NapCat 事件监听（HTTP=%s WS=%s）",
                     _env_str("ONEBOT_HTTP", "http://127.0.0.1:3000"),
                     _env_str("ONEBOT_WS", "ws://127.0.0.1:3001"))
            runner.run_forever()
            return

        gw = QQGateway(self.api, intents=INTENT_GROUP_AND_C2C_EVENT, on_event=self.on_event)
        log.info("启动 QQ 官方长连接（intents=%s）", INTENT_GROUP_AND_C2C_EVENT)
        gw.run_forever()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="雪之下雪乃 · QQ 官方机器人")
    ap.add_argument("--dry-run", action="store_true", help="只验证凭据+网关，不处理消息")
    ap.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    bot = QQBot(dry_run=args.dry_run)
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，退出")
    except QQBotError as exc:
        log.error("QQ 接口错误：%s", exc)
        return 2
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
