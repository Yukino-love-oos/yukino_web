# -*- coding: utf-8 -*-
"""NapCat / OneBot 通道 + 发送层抽象的测试。

切通道最怕的是「消息格式拼错了但发出去没报错」，所以这里测的是：
  · 事件解析（私聊/群聊@/自己发的/非消息）不能错判
  · 发送段（text/record/image）的类型和文件路径必须对
  · 两种 transport 的路由（c2c vs group）不能串
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.qq import onebot, transport  # noqa: E402


class TestExtractText(unittest.TestCase):
    def test_segments(self):
        msg = [
            {"type": "text", "data": {"text": "你好"}},
            {"type": "face", "data": {"id": "1"}},
            {"type": "text", "data": {"text": "呀"}},
        ]
        self.assertEqual(onebot.extract_text(msg), "你好呀")

    def test_cq_string_is_stripped(self):
        self.assertEqual(onebot.extract_text("[CQ:face,id=1]你好"), "你好")

    def test_empty(self):
        self.assertEqual(onebot.extract_text([]), "")
        self.assertEqual(onebot.extract_text(None), "")


class TestParseEvent(unittest.TestCase):
    def _private(self, **kw):
        base = {
            "post_type": "message",
            "message_type": "private",
            "self_id": 111,
            "user_id": 222,
            "message_id": 1,
            "message": [{"type": "text", "data": {"text": "你好"}}],
        }
        base.update(kw)
        return base

    def test_private_message(self):
        ev = onebot.parse_event(self._private())
        self.assertEqual(ev["kind"], "c2c")
        self.assertEqual(ev["target"], "222")
        self.assertEqual(ev["text"], "你好")
        self.assertEqual(ev["message_id"], "1")

    def test_own_message_ignored(self):
        self.assertIsNone(onebot.parse_event(self._private(user_id=111)))

    def test_group_without_at_ignored(self):
        raw = self._private(message_type="group", group_id=333)
        self.assertIsNone(onebot.parse_event(raw))

    def test_group_with_at(self):
        raw = self._private(
            message_type="group",
            group_id=333,
            message=[
                {"type": "at", "data": {"qq": "111"}},
                {"type": "text", "data": {"text": " 在吗"}},
            ],
        )
        ev = onebot.parse_event(raw)
        self.assertEqual(ev["kind"], "group")
        self.assertEqual(ev["target"], "333")
        self.assertEqual(ev["text"], "在吗")

    def test_non_message_post_type(self):
        self.assertIsNone(onebot.parse_event({"post_type": "notice", "notice_type": "x"}))

    def test_invalid_json_string(self):
        self.assertIsNone(onebot.parse_event("not json"))

    def test_empty_text_ignored(self):
        raw = self._private(message=[{"type": "image", "data": {"file": "a.png"}}])
        self.assertIsNone(onebot.parse_event(raw))

    def test_json_string_input(self):
        ev = onebot.parse_event(json.dumps(self._private()))
        self.assertEqual(ev["kind"], "c2c")


class FakeOneBotClient:
    def __init__(self):
        self.calls = []

    def send_private_msg(self, user_id, segments):
        self.calls.append(("private", user_id, segments))
        return {"status": "ok"}

    def send_group_msg(self, group_id, segments):
        self.calls.append(("group", group_id, segments))
        return {"status": "ok"}


class TestOneBotTransport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="yukino-ob-")
        self.client = FakeOneBotClient()
        self.t = transport.OneBotTransport(self.client, tmp_dir=self.tmp)

    def test_text_segment(self):
        self.t.send_text("c2c", "222", "1", "你好")
        kind, target, segs = self.client.calls[-1]
        self.assertEqual(kind, "private")
        self.assertEqual(target, "222")
        self.assertEqual(segs[0]["type"], "text")
        self.assertEqual(segs[0]["data"]["text"], "你好")

    def test_group_text_routes_to_group_api(self):
        self.t.send_text("group", "333", "1", "hi")
        self.assertEqual(self.client.calls[-1][0], "group")

    def test_voice_writes_silk_and_sends_record(self):
        ok = self.t.send_silk("c2c", "222", "1", b"\x02#!SILK_V3fake")
        self.assertTrue(ok)
        _, _, segs = self.client.calls[-1]
        self.assertEqual(segs[0]["type"], "record")
        uri = segs[0]["data"]["file"]
        self.assertTrue(uri.startswith("file://"))
        self.assertTrue(Path(uri.replace("file:///", "")).is_file())

    def test_image_segment(self):
        ok = self.t.send_image("c2c", "222", "1", b"\x89PNG fake")
        self.assertTrue(ok)
        _, _, segs = self.client.calls[-1]
        self.assertEqual(segs[0]["type"], "image")
        self.assertTrue(segs[0]["data"]["file"].startswith("file://"))

    def test_empty_text_not_sent(self):
        self.t.send_text("c2c", "222", "1", "   ")
        self.assertEqual(self.client.calls, [])


class FakeOfficialApi:
    def __init__(self):
        self.calls = []

    def send_c2c_text(self, target, text, msg_id, msg_seq=1):
        self.calls.append(("c2c_text", target, text))

    def send_group_text(self, target, text, msg_id, msg_seq=1):
        self.calls.append(("group_text", target, text))

    def upload_c2c_file(self, target, file_type=1, file_data=None, url=None, file_name=None, **kw):
        self.calls.append(("upload_c2c", file_type))
        return {"file_info": "info-c2c"}

    def upload_group_file(self, target, file_type=1, file_data=None, url=None, file_name=None, **kw):
        self.calls.append(("upload_group", file_type))
        return {"file_info": "info-group"}

    def send_c2c_voice(self, target, info, msg_id, msg_seq=2):
        self.calls.append(("c2c_voice", info))

    def send_group_voice(self, target, info, msg_id, msg_seq=2):
        self.calls.append(("group_voice", info))

    def send_c2c_media(self, target, info, msg_id, msg_seq=2):
        self.calls.append(("c2c_media", info))

    def send_group_media(self, target, info, msg_id, msg_seq=2):
        self.calls.append(("group_media", info))


class TestOfficialTransport(unittest.TestCase):
    def setUp(self):
        self.api = FakeOfficialApi()
        self.t = transport.OfficialTransport(self.api)

    def test_text_routing(self):
        self.t.send_text("c2c", "u", "m", "hi")
        self.assertEqual(self.api.calls[-1][0], "c2c_text")
        self.t.send_text("group", "g", "m", "hi")
        self.assertEqual(self.api.calls[-1][0], "group_text")

    def test_voice_uploads_then_sends(self):
        ok = self.t.send_silk("c2c", "u", "m", b"\x02#!SILK_V3xx")
        self.assertTrue(ok)
        kinds = [c[0] for c in self.api.calls]
        self.assertIn("upload_c2c", kinds)
        self.assertIn("c2c_voice", kinds)
        self.assertLess(kinds.index("upload_c2c"), kinds.index("c2c_voice"))
        # 语音上传必须用 file_type=3（富媒体里的语音类型）
        self.assertIn(("upload_c2c", 3), self.api.calls)

    def test_image_uploads_with_type_1(self):
        ok = self.t.send_image("group", "g", "m", b"\x89PNGxx")
        self.assertTrue(ok)
        self.assertIn(("upload_group", 1), self.api.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
