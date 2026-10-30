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
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import quote

from ..config import load_settings
from ..pipeline import build_pipeline
from ..tts.silence import squeeze_pauses
from . import silk
from .api import INTENT_GROUP_AND_C2C_EVENT, QQApi, QQBotError
from .gateway import QQGateway

log = logging.getLogger(__name__)

T_C2C = "C2C_MESSAGE_CREATE"
T_GROUP_AT = "GROUP_AT_MESSAGE_CREATE"


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
        self.app_id = _env_str("QQ_APP_ID")
        self.app_secret = _env_str("QQ_APP_SECRET")
        if not self.app_id or not self.app_secret:
            raise SystemExit(
                "缺少 QQ_APP_ID / QQ_APP_SECRET。\n"
                "在 .env 里加上：\n"
                "  QQ_APP_ID=你的AppID\n"
                "  QQ_APP_SECRET=你的AppSecret"
            )
        self.api = QQApi(self.app_id, self.app_secret)
        self.dry_run = dry_run
        self.voice_enabled = _env_bool("QQ_VOICE", True)
        self.max_voice = int(_env_str("QQ_MAX_VOICE", "2") or "2")
        # 短语间保留多长的停顿；实测 180ms 听感最自然（80/120/180 三档对比）
        self.pause_ms = int(_env_str("QQ_PAUSE_MS", "180") or "180")
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
    def _send_text(self, kind: str, target: str, mid: str, text: str) -> None:
        if not text.strip():
            return
        if kind == "c2c":
            self.api.send_c2c_text(target, text, mid, msg_seq=1)
        else:
            self.api.send_group_text(target, text, mid, msg_seq=1)
        log.info("已回文字（%s 字）", len(text))

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
        # 先压掉合成时插入的长停顿：实测能把 52% 静音压到 30% 左右，
        # 听感从「一顿一顿」变成连贯短句（见 app/tts/silence.py 的实测数据）。
        try:
            compressed = squeeze_pauses(wav, max_gap_ms=self.pause_ms)
            if compressed:
                wav = compressed
        except Exception as exc:  # noqa: BLE001
            log.debug("停顿压缩失败，用原始音频：%r", exc)
        data = silk.encode_wav_to_silk(wav)
        if not data:
            return False
        try:
            up = self._upload_voice(kind, target, data)
            info = (up or {}).get("file_info")
            if not info:
                log.warning("上传语音没拿到 file_info：%s", up)
                return False
            if kind == "c2c":
                self.api.send_c2c_voice(target, info, mid, msg_seq=seq)
            else:
                self.api.send_group_voice(target, info, mid, msg_seq=seq)
            return True
        except QQBotError as exc:
            log.warning("发语音失败（可能上限/格式问题）：%s", exc)
            return False

    # ---------------- 单条消息处理 ----------------
    def _handle(self, kind: str, target: str, mid: str, content: str) -> None:
        if self.pipeline is None:
            self._send_text(kind, target, mid, "（服务未就绪）")
            return

        t0 = time.time()

        # ---- 第一段：只出文字（跳过 TTS），尽快回 ----
        turn = None
        for ev in self.pipeline.respond(content, speak=False):
            if ev.get("type") == "done":
                turn = ev.get("turn")
            elif ev.get("type") == "error":
                log.warning("生成失败：%s", ev.get("message"))
        if turn is None:
            self._send_text(kind, target, mid, "……（有点说不出来）")
            return

        reply = (turn.zh or "").strip() or (turn.ja or "").strip()
        try:
            self._send_text(kind, target, mid, reply)
        except QQBotError as exc:
            log.warning("回文字失败：%s", exc)
            return
        log.info("文字已回，用时 %.1fs", time.time() - t0)

        # ---- 第二段：补齐语音（尽力而为，失败不影响文字）----
        if not self.voice_enabled:
            return
        sentences = [s for s in getattr(turn, "sentences", []) if s and s.strip()]
        if not sentences:
            return
        if not silk.available_backends():
            log.info("没有 SILK 编码器，跳过语音（文字已送达）")
            return
        seq = 2
        sent = 0
        for sentence in sentences[: self.max_voice]:
            try:
                wav = self.pipeline.speak(sentence)
            except Exception as exc:  # noqa: BLE001
                log.warning("合成失败：%r", exc)
                break
            if not wav:
                continue
            if self._send_voice(kind, target, mid, wav, seq):
                sent += 1
                seq += 1
                log.info("语音已发（第 %s 条）", sent)
            else:
                break
        log.info("本轮完成：文字+%s 条语音，总用时 %.1fs", sent, time.time() - t0)

    # ---------------- 入口 ----------------
    def run(self) -> None:
        if self.dry_run:
            log.info("dry-run：验证凭据与网关…")
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
        gw = QQGateway(self.api, intents=INTENT_GROUP_AND_C2C_EVENT, on_event=self.on_event)
        log.info("启动 QQ 长连接（intents=%s）", INTENT_GROUP_AND_C2C_EVENT)
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
