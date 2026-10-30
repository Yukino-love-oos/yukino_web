# -*- coding: utf-8 -*-
"""QQ 适配器的离线单元测试。

只测不需要网络的纯逻辑：
  · WebSocket 掩码与帧编解码（协议正确性，错了会连不上）
  · 消息去重（QQ 断线重连会重推事件，重复回复很尴尬）
  · SILK 后端探测（缺编码器时要能优雅降级为纯文字）
  · QQApi 的 payload 组装（用假 _request 拦截，不打真实接口）

真实凭据/网关连通性不在这里测 —— 那是 `python -m app.qq --dry-run` 的事。
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.qq import silk  # noqa: E402
from app.qq.api import QQApi, QQBotError  # noqa: E402
from app.qq.gateway import OP_PING, OP_TEXT, WebSocket, _mask  # noqa: E402


class TestMask(unittest.TestCase):
    def test_mask_is_xor_and_involutive(self):
        key = b"\x01\x02\x03\x04"
        payload = b"hello world"
        masked = _mask(payload, key)
        self.assertNotEqual(masked, payload)
        self.assertEqual(_mask(masked, key), payload)

    def test_mask_empty(self):
        self.assertEqual(_mask(b"", b"\x00\x00\x00\x00"), b"")

    def test_mask_key_wraps(self):
        key = b"\xaa\xbb\xcc\xdd"
        payload = bytes(range(9))
        masked = _mask(payload, key)
        for i in range(len(payload)):
            self.assertEqual(masked[i], payload[i] ^ key[i % 4])


class TestFrameEncoding(unittest.TestCase):
    """把 _send_frame 的输出抓下来再解析，验证帧格式符合 RFC 6455。"""

    def _capture(self, ws, opcode, payload):
        sent = []
        ws._sock = type("S", (), {"sendall": lambda self, b: sent.append(bytes(b))})()
        ws._send_frame(opcode, payload)
        ws._sock = None
        return b"".join(sent)

    def test_small_frame(self):
        ws = WebSocket("ws://example.invalid/")
        raw = self._capture(ws, OP_TEXT, b"hi")
        self.assertEqual(raw[0], 0x80 | OP_TEXT)  # FIN + text
        self.assertTrue(raw[1] & 0x80)  # 客户端必须掩码
        self.assertEqual(raw[1] & 0x7F, 2)
        key = raw[2:6]
        self.assertEqual(_mask(raw[6:], key), b"hi")

    def test_medium_frame_uses_126(self):
        ws = WebSocket("ws://example.invalid/")
        payload = b"x" * 300
        raw = self._capture(ws, OP_TEXT, payload)
        self.assertEqual(raw[1] & 0x7F, 126)
        import struct

        declared = struct.unpack("!H", raw[2:4])[0]
        self.assertEqual(declared, 300)

    def test_large_frame_uses_127(self):
        ws = WebSocket("ws://example.invalid/")
        payload = b"y" * 70000
        raw = self._capture(ws, OP_TEXT, payload)
        self.assertEqual(raw[1] & 0x7F, 127)
        import struct

        declared = struct.unpack("!Q", raw[2:10])[0]
        self.assertEqual(declared, 70000)


class TestQQApiPayload(unittest.TestCase):
    def setUp(self):
        self.api = QQApi("1000", "secret")
        self.calls = []

        def fake_request(method, path, payload=None):
            self.calls.append((method, path, payload))
            return {"file_info": "abc"}

        self.api._request = fake_request  # type: ignore[assignment]

    def test_c2c_text_payload(self):
        self.api.send_c2c_text("openid-1", "你好", "msg-1", msg_seq=3)
        method, path, payload = self.calls[-1]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/v2/users/openid-1/messages")
        self.assertEqual(payload["content"], "你好")
        self.assertEqual(payload["msg_type"], 0)
        self.assertEqual(payload["msg_id"], "msg-1")
        self.assertEqual(payload["msg_seq"], 3)

    def test_group_text_path(self):
        self.api.send_group_text("gid-9", "hi", "m-2")
        _, path, payload = self.calls[-1]
        self.assertEqual(path, "/v2/groups/gid-9/messages")
        self.assertEqual(payload["msg_type"], 0)

    def test_voice_upload_uses_base64_file_data(self):
        self.api.upload_c2c_file("openid-1", file_data=b"\x02#!AMR", file_type=3)
        _, path, payload = self.calls[-1]
        self.assertEqual(path, "/v2/users/openid-1/files")
        self.assertEqual(payload["file_type"], 3)
        self.assertFalse(payload["srv_send_msg"])
        import base64

        self.assertEqual(base64.b64decode(payload["file_data"]), b"\x02#!AMR")

    def test_voice_upload_requires_data_or_url(self):
        with self.assertRaises(QQBotError):
            self.api.upload_c2c_file("openid-1")

    def test_voice_message_shape(self):
        self.api.send_c2c_voice("openid-1", "file-info-x", "msg-3", msg_seq=2)
        _, path, payload = self.calls[-1]
        self.assertEqual(path, "/v2/users/openid-1/messages")
        self.assertEqual(payload["msg_type"], 7)
        self.assertEqual(payload["media"]["file_info"], "file-info-x")
        self.assertEqual(payload["msg_seq"], 2)

    def test_missing_credentials_raise(self):
        with self.assertRaises(QQBotError):
            QQApi("", "secret")


class TestSilk(unittest.TestCase):
    def test_backends_list_is_a_list(self):
        self.assertIsInstance(silk.available_backends(), list)

    def test_empty_input_returns_none(self):
        self.assertIsNone(silk.encode_wav_to_silk(b""))

    def test_bad_wav_returns_none_not_raise(self):
        self.assertIsNone(silk.encode_wav_to_silk(b"not a wav at all"))

    def test_wav_to_pcm_resamples_mono(self):
        import io
        import struct
        import wave

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(32000)
            w.writeframes(struct.pack("<1000h", *([1000] * 1000)))
        pcm = silk.wav_to_pcm(buf.getvalue())
        # 32k -> 24k，样本数约 750
        self.assertGreater(len(pcm), 1400)
        self.assertLess(len(pcm), 1600)


class TestDedupe(unittest.TestCase):
    def test_duplicate_message_id_rejected(self):
        # 只测去重逻辑，绕开需要凭据的构造函数
        from app.qq.bot import QQBot

        bot = QQBot.__new__(QQBot)
        import threading

        bot._seen = {}
        bot._seen_lock = threading.Lock()
        self.assertTrue(bot._first_time("m1"))
        self.assertFalse(bot._first_time("m1"))
        self.assertTrue(bot._first_time("m2"))
        self.assertTrue(bot._first_time(""))  # 空 id 不拦


if __name__ == "__main__":
    unittest.main(verbosity=2)
