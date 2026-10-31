"""发送层抽象：同一套对话逻辑，挂两种 QQ 通道。

为什么要有这一层
----------------
官方机器人（WebSocket 网关 + OpenAPI）和 NapCat（个人号 + OneBot 11）
的「怎么发」完全不同，但「发什么」完全一样：
  一条文字 / 一段语音 / 一张表情包。

所以把差异收敛到 transport：

    BaseTransport
      ├─ OfficialTransport  官方机器人：上传富媒体拿 file_info，再 msg_type=7
      └─ OneBotTransport    NapCat：直接发 message 段（text / record / image）

bot.py 只跟 transport 打交道，切换通道就是换一个实现，不改业务代码。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)


class BaseTransport:
    """发送接口。kind 只有 "c2c"（私聊）和 "group"（群聊）两种。"""

    name = "base"

    def send_text(self, kind: str, target: str, msg_id: str, text: str, seq: int = 1) -> None:
        raise NotImplementedError

    def send_silk(self, kind: str, target: str, msg_id: str, silk: bytes, seq: int = 2) -> bool:
        raise NotImplementedError

    def send_image(self, kind: str, target: str, msg_id: str, png: bytes, seq: int = 2) -> bool:
        raise NotImplementedError


class OfficialTransport(BaseTransport):
    """QQ 官方机器人通道（当前在生产用的那条）。"""

    name = "official"

    def __init__(self, api, *, media_base: str = "", media_token: str = ""):
        self.api = api
        self.media_base = (media_base or "").rstrip("/")
        self.media_token = media_token or ""
        self.media_dir = Path(__file__).resolve().parents[2] / "data" / "qq_media"

    # ---------------- 文字 ----------------
    def send_text(self, kind: str, target: str, msg_id: str, text: str, seq: int = 1) -> None:
        if not text.strip():
            return
        if kind == "c2c":
            self.api.send_c2c_text(target, text, msg_id, msg_seq=seq)
        else:
            self.api.send_group_text(target, text, msg_id, msg_seq=seq)
        log.info("已回文字（%s 字, seq=%s）", len(text), seq)

    # ---------------- 富媒体 ----------------
    def _publish(self, data: bytes, suffix: str) -> str | None:
        """把文件落到 web 服务的 data/qq_media 下，返回公网 URL。

        官方接口对 base64(file_data) 送语音会返回 850019「富媒体文件格式不支持」，
        需要一个腾讯服务器能拉取的 url。我们本来就有公网 web 服务，顺手当临时文件服务器。
        """
        if not self.media_base:
            return None
        try:
            self.media_dir.mkdir(parents=True, exist_ok=True)
            name = "y%s%s" % (uuid.uuid4().hex, suffix)
            (self.media_dir / name).write_bytes(data)
            try:
                olds = sorted(self.media_dir.glob("*" + suffix), key=lambda p: p.stat().st_mtime)
                for old in olds[:-16]:
                    old.unlink()
            except OSError:
                pass
        except OSError as exc:
            log.warning("写临时文件失败：%r", exc)
            return None
        url = "%s/media/%s" % (self.media_base, name)
        if self.media_token:
            url += "?t=" + quote(self.media_token, safe="")
        return url

    def _upload(self, kind: str, file_type: int, data: bytes, filename: str) -> dict:
        from .api import QQBotError

        try:
            if kind == "c2c":
                return self.api.upload_c2c_file(
                    target="", file_type=file_type, file_data=data, file_name=filename
                )
            return self.api.upload_group_file(
                target="", file_type=file_type, file_data=data, file_name=filename
            )
        except TypeError:
            raise
        except QQBotError:
            raise

    def _upload_to(self, kind: str, target: str, file_type: int, data: bytes, filename: str) -> dict:
        from .api import QQBotError

        try:
            if kind == "c2c":
                return self.api.upload_c2c_file(
                    target, file_type=file_type, file_data=data, file_name=filename
                )
            return self.api.upload_group_file(
                target, file_type=file_type, file_data=data, file_name=filename
            )
        except QQBotError as exc:
            url = self._publish(data, ".silk" if file_type == 3 else ".png")
            if not url:
                raise QQBotError("base64 与 url 两种上传方式都不可用：%s" % exc) from exc
            log.warning("base64 上传失败，改用公网 url：%s", exc)
            if kind == "c2c":
                return self.api.upload_c2c_file(target, file_type=file_type, url=url)
            return self.api.upload_group_file(target, file_type=file_type, url=url)

    def send_silk(self, kind: str, target: str, msg_id: str, silk: bytes, seq: int = 2) -> bool:
        from .api import QQBotError

        try:
            up = self._upload_to(kind, target, 3, silk, "yukino.silk")
            info = (up or {}).get("file_info")
            if not info:
                log.warning("上传语音没拿到 file_info：%s", up)
                return False
            if kind == "c2c":
                self.api.send_c2c_voice(target, info, msg_id, msg_seq=seq)
            else:
                self.api.send_group_voice(target, info, msg_id, msg_seq=seq)
            return True
        except QQBotError as exc:
            log.warning("发语音失败（可能上限/格式问题）：%s", exc)
            return False

    def send_image(self, kind: str, target: str, msg_id: str, png: bytes, seq: int = 2) -> bool:
        from .api import QQBotError

        try:
            up = self._upload_to(kind, target, 1, png, "sticker.png")
            info = (up or {}).get("file_info")
            if not info:
                log.warning("上传表情包没拿到 file_info：%s", up)
                return False
            if kind == "c2c":
                self.api.send_c2c_media(target, info, msg_id, msg_seq=seq)
            else:
                self.api.send_group_media(target, info, msg_id, msg_seq=seq)
            return True
        except QQBotError as exc:
            log.warning("发表情包失败：%s", exc)
            return False


class OneBotTransport(BaseTransport):
    """NapCat / OneBot 11 通道（个人号）。

    跟官方通道的区别：
      · 不需要上传拿 file_info，直接发 message 段
      · 语音用 `record` 段，文件路径（file://）由 NapCat 自己读
      · 没有 msg_seq 这种被动回复限制，msg_id 参数保留只是为了接口统一
    """

    name = "onebot"

    def __init__(self, client, *, tmp_dir: str | Path | None = None):
        self.client = client
        self.tmp_dir = Path(tmp_dir) if tmp_dir else Path(__file__).resolve().parents[2] / "data" / "qq_media"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self._sent = 0

    def _write(self, data: bytes, suffix: str) -> str:
        name = "ob%s%s" % (uuid.uuid4().hex, suffix)
        path = self.tmp_dir / name
        path.write_bytes(data)
        # 只留最近 24 个，避免堆积
        try:
            olds = sorted(self.tmp_dir.glob("ob*"), key=lambda p: p.stat().st_mtime)
            for old in olds[:-24]:
                old.unlink()
        except OSError:
            pass
        return path.resolve().as_uri()   # file:///C:/...

    def _send(self, kind: str, target: str, segments: list[dict]) -> bool:
        try:
            if kind == "c2c":
                self.client.send_private_msg(target, segments)
            else:
                self.client.send_group_msg(target, segments)
            self._sent += 1
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("OneBot 发送失败：%r", exc)
            return False

    def send_text(self, kind: str, target: str, msg_id: str, text: str, seq: int = 1) -> None:
        if not text.strip():
            return
        if not self._send(kind, target, [{"type": "text", "data": {"text": text}}]):
            raise RuntimeError("OneBot 文字发送失败")
        log.info("已回文字（%s 字）", len(text))

    def send_silk(self, kind: str, target: str, msg_id: str, silk: bytes, seq: int = 2) -> bool:
        try:
            uri = self._write(silk, ".silk")
        except OSError as exc:
            log.warning("写临时 SILK 失败：%r", exc)
            return False
        ok = self._send(kind, target, [{"type": "record", "data": {"file": uri}}])
        if ok:
            log.info("语音已发（%.1f KB）", len(silk) / 1024.0)
        return ok

    def send_image(self, kind: str, target: str, msg_id: str, png: bytes, seq: int = 2) -> bool:
        try:
            uri = self._write(png, ".png")
        except OSError as exc:
            log.warning("写临时图片失败：%r", exc)
            return False
        ok = self._send(kind, target, [{"type": "image", "data": {"file": uri}}])
        if ok:
            log.info("表情包已发（%.1f KB）", len(png) / 1024.0)
        return ok
