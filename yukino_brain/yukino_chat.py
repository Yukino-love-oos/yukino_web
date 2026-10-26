"""⚠ 本文件已废弃，仅为兼容旧调用方式保留。

原来的 yukino_chat.py 有两个问题：
  1. DeepSeek API Key 明文写在源码里，且已随仓库推送到 GitHub（需立刻吊销）
  2. 「好感度」是模块级全局变量，重启归零；输出解析靠 `[AFFINITY_CHANGE: N]`
     和正则切括号，非常脆弱

请改用新实现：

    python -m app.cli                 # 终端对话（零第三方依赖）
    python -m app.cli --mock          # 离线假模型，验证链路
    .\\scripts\\run.ps1 -Cli          # 一键启动（会自动拉起语音服务）

新实现的位置：
    app/config.py           配置（从 .env 读，密钥不再进代码）
    app/llm/persona.py      人格卡 persona/yukino.json
    app/llm/protocol.py     标签协议解析
    app/llm/client.py       OpenAI 兼容流式客户端（标准库实现）
    app/state/session.py    好感度持久化 + 代码级防刷分
    app/tts/gsv.py          GPT-SoVITS 客户端（修正了 ref_audio_path / 参数名）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    print(__doc__)
    from app.cli import main as new_main

    print("—— 已自动转入新实现 ——\n")
    return new_main()


if __name__ == "__main__":
    raise SystemExit(main())
