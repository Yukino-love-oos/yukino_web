"""⚠ 本文件已废弃，仅为兼容旧调用方式保留。

原来的 yukino_web.py（Streamlit 版）有三个致命问题：
  1. API Key 明文写在源码里，且已随仓库推送到 GitHub（需立刻吊销）
  2. 语音链路是断的：
       · startAPI.bat 启动的是 api.py，但代码用的是 api_v2.py 的参数名
         （text_lang / prompt_lang / speed_factor vs text_language / prompt_language / speed）
       · 完全没传 ref_audio_path（api_v2 必填），导致参考音频未就绪
       · 传了 api_v2 没有的 spk 参数
     结果：yukino_say() 永远拿到非 200 → return None → 一句都听不到
  3. 动作描写/翻译/台词混在一个字符串里用正则拆，句中一出现括号就漏进 TTS

请改用新实现：

    .\\scripts\\run.ps1                # 网页版（自动拉起语音服务）
    python -m streamlit run app/ui/streamlit_app.py
    python -m app.cli                 # 终端版（零依赖）

新实现对应的文件：
    app/ui/streamlit_app.py     界面（立绘随 <mood> 变表情）
    app/pipeline.py             流式编排：LLM → 标签协议 → 分句 → TTS
    app/tts/gsv.py              修正后的 GPT-SoVITS 客户端
    configs/tts_infer_yukino.yaml  正确的权重配对与 CUDA 配置
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    print(__doc__)
    answer = input("现在启动新的网页版吗？[Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        import subprocess

        cmd = [sys.executable, "-m", "streamlit", "run", str(ROOT / "app/ui/streamlit_app.py")]
        print("执行：", " ".join(cmd))
        return subprocess.call(cmd)
    print("已取消。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
