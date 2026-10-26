"""雪之下雪乃 AI 语音伴侣 —— 应用层。

设计原则：
  1. 核心链路（配置 / 协议解析 / 状态 / TTS 客户端）**只用标准库**，
     不依赖 openai / pydantic / requests，装不上包也能跑，也便于就地测试。
  2. 唯一的强依赖是 streamlit（UI）。终端版 app/cli.py 零依赖。
"""

__version__ = "2.0.0"
