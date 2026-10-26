"""极简 streamlit 桩模块：只为在没有装 streamlit 时验证 UI 代码路径不报错。

不是模拟真实渲染，只保证：
  · 所有属性和方法都存在（不会 AttributeError）
  · st.sidebar.xxx 也能正确路由到同一批桩函数
  · 上下文管理器可用、columns() 返回可解包的多列
  · chat_input() 返回脚本化输入，让「处理输入」整条分支真的跑一遍
  · st.cache_resource 像真的一样可调用、可当装饰器、且有 .clear()
"""

from __future__ import annotations

import os
import sys

_SCRIPTED_INPUT = os.environ.get("STUB_CHAT_INPUT", "")
_calls: list[str] = []


class _Stub:
    def __init__(self, name: str = "stub") -> None:
        self._name = name

    def __getattr__(self, item: str):
        if item.startswith("__"):
            raise AttributeError(item)
        return _Stub(f"{self._name}.{item}")

    def __call__(self, *args, **kwargs):
        _calls.append(self._name)
        return _Stub(f"{self._name}()")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter([])

    def __bool__(self):
        return True

    def __repr__(self):
        return f"<Stub {self._name}>"


class _Sidebar:
    """st.sidebar 的任何属性都优先路由到本模块同名桩函数。"""

    def __getattr__(self, item: str):
        if item.startswith("__"):
            raise AttributeError(item)
        fn = getattr(sys.modules[__name__], item, None)
        if callable(fn):
            return fn
        return _Stub(f"sidebar.{item}")


class _CacheResource:
    """模仿 st.cache_resource：可当装饰器、可带参数、可用 .clear()。"""

    def __call__(self, fn=None, **kwargs):
        if callable(fn):
            return fn
        return lambda f: f

    def clear(self):
        _calls.append("cache_resource.clear()")


sidebar = _Sidebar()
cache_resource = _CacheResource()


# ---------------- 无返回值的桩 ----------------
def _rec(name):
    _calls.append(name)


def set_page_config(*a, **k):
    _rec("set_page_config")


def title(*a, **k):
    _rec("title")


def header(*a, **k):
    _rec("header")


def subheader(*a, **k):
    _rec("subheader")


def caption(*a, **k):
    _rec("caption")


def markdown(*a, **k):
    _rec("markdown")


def write(*a, **k):
    _rec("write")


def text(*a, **k):
    _rec("text")


def code(*a, **k):
    _rec("code")


def divider(*a, **k):
    _rec("divider")


def success(*a, **k):
    _rec("success")


def warning(*a, **k):
    _rec("warning")


def error(*a, **k):
    _rec("error")


def info(*a, **k):
    _rec("info")


def toast(*a, **k):
    _rec("toast")


def progress(*a, **k):
    _rec("progress")


def metric(*a, **k):
    _rec("metric")


def image(*a, **k):
    _rec("image")


def stop():
    raise SystemExit("st.stop()")


def rerun(*a, **k):
    _rec("rerun")


# ---------------- 有返回值的桩 ----------------
def columns(spec, **k):
    n = spec if isinstance(spec, int) else len(spec)
    _rec("columns")
    return [_Stub(f"col{i}") for i in range(n)]


def container(*a, **k):
    return _Stub("container")


def expander(*a, **k):
    return _Stub("expander")


def empty():
    return _Stub("empty")


def spinner(*a, **k):
    return _Stub("spinner")


def chat_message(role, **k):
    return _Stub(f"chat_message[{role}]")


def chat_input(*a, **k):
    _rec("chat_input")
    return _SCRIPTED_INPUT or None


def toggle(label, value=False, **k):
    _rec(f"toggle[{label}]")
    return value


def checkbox(label, value=False, **k):
    _rec(f"checkbox[{label}]")
    return value


def button(*a, **k):
    _rec("button")
    return False


def audio(data=None, **k):
    _rec(f"audio[{len(data) if data else 0}B]")
    # 模拟老版本 streamlit 没有 autoplay 参数
    if "autoplay" in k and os.environ.get("STUB_NO_AUTOPLAY") == "1":
        raise TypeError("audio() got an unexpected keyword argument 'autoplay'")


def get_calls() -> list[str]:
    return list(_calls)
