"""用假 streamlit 跑一遍 app/ui/streamlit_app.py，验证 UI 代码路径。

用法：
    set PYTHONPATH=D:\\Yukino\\tmp\\stub;D:\\Yukino
    set STUB_CHAT_INPUT=今天有点累
    python tmp/ui_smoke.py
"""

import runpy
import sys
import traceback

APP = r"D:\Yukino\app\ui\streamlit_app.py"

try:
    runpy.run_path(APP, run_name="__main__")
except SystemExit as e:
    print(f"[st.stop 触发] {e}")
except Exception:
    print("=== UI 冒烟测试失败 ===")
    traceback.print_exc()
    sys.exit(1)

import streamlit as st  # noqa: E402  （此时是桩模块）

calls = st.get_calls() if hasattr(st, "get_calls") else []
print("=== UI 冒烟测试通过 ===")
print(f"共记录 {len(calls)} 次 st.* 调用")

audio = [c for c in calls if "audio" in c]
print(f"audio 相关调用 {len(audio)} 次: {audio[:6]}")
print(f"chat_message 渲染 {sum(1 for c in calls if c.startswith('chat_message'))} 次")
print(f"caption（动作/翻译/好感度）{sum(1 for c in calls if c.startswith('caption'))} 次")
print(f"code（system prompt 展开）{sum(1 for c in calls if c.startswith('code'))} 次")
for key in ("set_page_config", "title", "chat_input", "metric", "progress", "expander"):
    print(f"  {key:18} = {sum(1 for c in calls if c.startswith(key))}")

if not audio:
    print("\n⚠ 本轮没有任何 audio 调用 —— 可能是语音服务未启动")
    sys.exit(2)
