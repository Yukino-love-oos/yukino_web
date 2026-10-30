"""最小可用的 WebSocket 客户端（纯 stdlib）+ QQ 网关会话。

为什么要自己写而不是用 websockets 库：
  项目核心承诺是「零第三方依赖」，这样在只装了 Python 的机器上也能跑，
  包括我们这台从零重建的云服务器。QQ 网关只需要最基本的文本帧 + ping/pong，
  RFC 6455 里这一小部分加起来不到 200 行，不值得引入依赖。

支持：
  · wss://（TLS）
  · 文本/二进制帧、分片续帧、ping/pong、close
  · 客户端掩码（协议强制要求）

不支持（用不到）：
  · 扩展协商（permessage-deflate 等）、子协议
"""

from __future__ import annotations

import base64
import json
import logging
import os
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# QQ 网关 opcode
GW_DISPATCH = 0
GW_HEARTBEAT = 1
GW_IDENTIFY = 2
GW_RESUME = 6
GW_RECONNECT = 7
GW_INVALID_SESSION = 9
GW_HELLO = 10
GW_HEARTBEAT_ACK = 11


class WebSocketError(RuntimeError):
    pass


def _mask(payload: bytes, key: bytes) -> bytes:
    # 用 XOR 逐字节掩码；int.from_bytes 的循环比纯 python 快一些
    n = len(payload)
    if n == 0:
        return payload
    return bytes(payload[i] ^ key[i % 4] for i in range(n))


class WebSocket:
    """阻塞式 WebSocket 客户端。逐帧同步收发，够用且好调。"""

    def __init__(self, url: str, *, timeout: float = 30.0):
        self.url = url
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""

    # ---------------- 连接 ----------------
    def connect(self) -> None:
        u = urlparse(self.url)
        if u.scheme not in ("ws", "wss"):
            raise WebSocketError(f"不支持的 scheme：{u.scheme}")
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "wss" else 80)
        path = u.path or "/"
        if u.query:
            path += "?" + u.query

        raw = socket.create_connection((host, port), timeout=self.timeout)
        if u.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        self._sock = raw

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(req.encode("ascii"))

        # 读到 \r\n\r\n 为止
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WebSocketError("握手期间连接被关闭")
            head += chunk
        header, _, rest = head.partition(b"\r\n\r\n")
        first = header.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in first:
            raise WebSocketError(f"握手失败：{first}")
        self._buf = rest
        log.debug("WebSocket 已连接 %s", host)

    # ---------------- 底层帧 ----------------
    def _recv_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            if self._sock is None:
                raise WebSocketError("连接已关闭")
            chunk = self._sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WebSocketError("连接被对端关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._sock is None:
            raise WebSocketError("连接已关闭")
        header = bytearray()
        header.append(0x80 | opcode)  # FIN + opcode
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < (1 << 16):
            header.append(0x80 | 126)
            header += struct.pack("!H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", n)
        key = os.urandom(4)
        header += key
        self._sock.sendall(bytes(header) + _mask(payload, key))

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def _recv_frame(self) -> tuple[int, bytes]:
        b0, b1 = self._recv_exact(2)
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        key = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked and key:
            payload = _mask(payload, key)
        if not fin:
            # 分片：把后续帧拼起来
            parts = [payload]
            while True:
                b0b, b1b = self._recv_exact(2)
                op2 = b0b & 0x0F
                fin2 = bool(b0b & 0x80)
                l2 = b1b & 0x7F
                if l2 == 126:
                    l2 = struct.unpack("!H", self._recv_exact(2))[0]
                elif l2 == 127:
                    l2 = struct.unpack("!Q", self._recv_exact(8))[0]
                k2 = self._recv_exact(4) if (b1b & 0x80) else b""
                p2 = self._recv_exact(l2) if l2 else b""
                if k2:
                    p2 = _mask(p2, k2)
                parts.append(p2)
                if fin2:
                    payload = b"".join(parts)
                    break
                if op2 == OP_CONT:
                    continue
            # 这里 opcode 沿用首帧的
        return opcode, payload

    def recv_text(self) -> str | None:
        """收一条文本消息；自动处理 ping/pong/close。超时返回 None。"""
        while True:
            try:
                opcode, payload = self._recv_frame()
            except (socket.timeout, TimeoutError):
                return None
            if opcode == OP_PING:
                try:
                    self._send_frame(OP_PONG, payload)
                except OSError:
                    raise WebSocketError("回 pong 失败") from None
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                raise WebSocketError("对端关闭了连接")
            if opcode in (OP_TEXT, OP_CONT):
                return payload.decode("utf-8", "replace")
            if opcode == OP_BINARY:
                continue  # QQ 网关不推二进制
            log.debug("忽略未知 opcode %s", opcode)

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self._sock.close()
        finally:
            self._sock = None


class QQGateway:
    """QQ 机器人网关会话：identify -> heartbeat -> dispatch 回调。"""

    def __init__(self, api, *, intents: int, on_event, on_log=None):
        self.api = api
        self.intents = intents
        self.on_event = on_event
        self.on_log = on_log or (lambda msg: log.info("%s", msg))
        self.ws: WebSocket | None = None
        self.seq: int | None = None
        self.session_id: str | None = None
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    # ---------------- 心跳 ----------------
    def _start_heartbeat(self, interval_ms: int) -> None:
        self._hb_stop.set()
        if self._hb_thread and self._hb_thread.is_alive():
            self._hb_thread.join(timeout=2)
        self._hb_stop = threading.Event()
        interval = max(1.0, interval_ms / 1000.0)

        def beat():
            ws = self.ws
            while not self._hb_stop.wait(interval):
                if ws is None:
                    return
                try:
                    ws.send_text(json.dumps({"op": GW_HEARTBEAT, "d": self.seq}))
                except Exception as exc:  # noqa: BLE001
                    self.on_log(f"心跳发送失败：{exc!r}")
                    return

        self._hb_thread = threading.Thread(target=beat, name="qq-heartbeat", daemon=True)
        self._hb_thread.start()

    def _stop_heartbeat(self) -> None:
        self._hb_stop.set()

    # ---------------- 会话 ----------------
    def identify(self) -> None:
        token = self.api.access_token()
        self.ws.send_text(
            json.dumps(
                {
                    "op": GW_IDENTIFY,
                    "d": {
                        "token": f"QQBot {token}",
                        "intents": self.intents,
                        "shard": [0, 1],
                        "properties": {
                            "$os": "Windows",
                            "$browser": "yukino",
                            "$device": "yukino",
                        },
                    },
                }
            )
        )

    def connect(self) -> None:
        url = self.api.gateway_url()
        self.ws = WebSocket(url, timeout=60)
        self.ws.connect()
        self.on_log(f"网关已连接：{url}")

    def close(self) -> None:
        self._stop_heartbeat()
        if self.ws:
            self.ws.close()
            self.ws = None

    def run_once(self) -> str:
        """连一次、跑到断开为止。返回下次应该做什么：'reconnect' | 'stop'。"""
        self.connect()
        hello_seen = False
        while True:
            raw = self.ws.recv_text()
            if raw is None:
                continue  # 收包超时，继续等（心跳在另一线程）
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("网关送来无法解析的数据：%r", raw[:200])
                continue
            op = msg.get("op")
            if msg.get("s") is not None:
                self.seq = msg["s"]

            if op == GW_HELLO:
                interval = int((msg.get("d") or {}).get("heartbeat_interval", 30000))
                self._start_heartbeat(interval)
                if not hello_seen:
                    self.identify()
                    hello_seen = True
                else:
                    self._resume_if_possible()
            elif op == GW_HEARTBEAT_ACK:
                continue
            elif op == GW_DISPATCH:
                t = msg.get("t") or ""
                d = msg.get("d") or {}
                if t == "READY":
                    self.session_id = d.get("session_id") or self.session_id
                    user = d.get("user") or {}
                    self.on_log(
                        "登录成功：bot=%s  session=%s"
                        % (user.get("username") or user.get("id") or "?", self.session_id)
                    )
                elif t == "RESUMED":
                    self.on_log("会话已恢复")
                try:
                    self.on_event(t, d)
                except Exception:  # noqa: BLE001
                    log.exception("事件处理失败：%s", t)
            elif op == GW_RECONNECT:
                self.on_log("网关要求重连")
                self._stop_heartbeat()
                return "reconnect"
            elif op == GW_INVALID_SESSION:
                self.on_log("会话无效，需要重新 identify")
                self._stop_heartbeat()
                self.session_id = None
                return "reconnect"
            else:
                log.debug("未处理的 op=%s", op)

    def _resume_if_possible(self) -> None:
        if not self.session_id:
            self.identify()
            return
        token = self.api.access_token()
        self.ws.send_text(
            json.dumps(
                {
                    "op": GW_RESUME,
                    "d": {
                        "token": f"QQBot {token}",
                        "session_id": self.session_id,
                        "seq": self.seq or 0,
                    },
                }
            )
        )
        self.on_log("尝试恢复会话…")

    def run_forever(self, *, max_backoff: float = 30.0) -> None:
        backoff = 1.0
        while True:
            try:
                action = self.run_once()
                if action == "stop":
                    return
                backoff = 1.0
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001
                self.on_log(f"网关异常：{exc!r}，{backoff:.0f}s 后重连")
                time.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)
            finally:
                self._stop_heartbeat()
                if self.ws:
                    try:
                        self.ws.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self.ws = None
