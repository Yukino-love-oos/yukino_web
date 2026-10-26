"""终端版对话 —— 零依赖（只用标准库），装不上任何包也能跑。

用法：
    python -m app.cli                 # 正式对话（需要 .env 里的 DEEPSEEK_API_KEY）
    python -m app.cli --mock          # 离线假模型，验证链路
    python -m app.cli --mock --once   # 跑一句就退出（冒烟测试）
    python -m app.cli --no-audio      # 只要文字
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def play_wav_bytes(data: bytes) -> bool:
    """播放 WAV 字节。Windows 用 winsound，其他平台回退到临时文件 + 系统播放器。"""
    if not data:
        return False
    try:
        import winsound  # Windows 标准库

        winsound.PlaySound(data, winsound.SND_MEMORY)
        return True
    except Exception:
        pass
    try:
        import platform
        import subprocess
        import tempfile

        suffix = ".wav"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            f.write(data)
            path = f.name
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["afplay", path], check=False)
        else:
            subprocess.run(["aplay", path], check=False)
        os.unlink(path)
        return True
    except Exception:
        return False


def _bar(value: int, lo: int = -100, hi: int = 100, width: int = 20) -> str:
    pos = int((value - lo) / (hi - lo) * width)
    pos = max(0, min(width - 1, pos))
    return "[" + "·" * pos + "◆" + "·" * (width - pos - 1) + "]"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="雪之下雪乃 · 终端对话")
    ap.add_argument("--mock", action="store_true", help="使用离线假模型（不消耗 API）")
    ap.add_argument("--no-audio", action="store_true", help="不播放语音")
    ap.add_argument("--once", metavar="TEXT", help="只处理一句话然后退出")
    args = ap.parse_args(argv)

    if args.mock:
        os.environ["YUKINO_MOCK_LLM"] = "1"

    from .config import load_settings
    from .pipeline import build_pipeline

    settings = load_settings()

    print("=" * 58)
    print("  雪之下雪乃 · 侍奉部")
    print("=" * 58)
    provider = getattr(pipeline.llm, "provider", None) or (
        "mock" if type(pipeline.llm).__name__ == "MockClient" else settings.llm_provider
    )
    model = getattr(pipeline.llm, "model", settings.model)
    print(f"  思考模型 : {provider} / {model}")
    print(f"  语音     : {settings.gsv_api_base}")
    print(f"  好感度   : {settings.db_path}")
    print("=" * 58)

    pipeline = build_pipeline(settings)

    if not args.no_audio and not pipeline.tts.health():
        print("⚠ 语音服务未启动，本次只用文字。启动方式： .\\scripts\\start_tts.ps1")
        print()

    # 开场白
    if pipeline.persona.greetings:
        greet = pipeline.persona.greetings[0]
        print(f"\n雪乃：{greet}")

    def handle(text: str) -> None:
        t0 = time.time()
        first_audio_at: float | None = None
        spoke = False

        print()
        for ev in pipeline.respond(text):
            kind = ev["type"]
            if kind == "sentence":
                if first_audio_at is None and ev["audio"]:
                    first_audio_at = time.time() - t0
                if ev["audio"] and not args.no_audio:
                    if not spoke:
                        sys.stdout.write("\n雪乃：")
                        spoke = True
                    if play_wav_bytes(ev["audio"]) is False:
                        pass  # 播放失败就静默，文字照常显示
            elif kind == "error":
                print(f"\n[错误] {ev['message']}")
            elif kind == "done":
                turn = ev["turn"]
                if not spoke:
                    print(f"\n雪乃：{turn.ja}")
                print(f"\n      （{turn.act}）" if turn.act else "")
                if turn.zh:
                    print(f"      {turn.zh}")
                delta = ev["delta"]
                sign = f"{delta:+d}" if delta else "±0"
                reason = f"   ← {ev['reason']}" if ev["reason"] else ""
                print(f"\n      {pipeline.persona.name} ${sign}  好感度 {ev['affinity']} "
                      f"{_bar(ev['affinity'])} {ev['stats']['band']}{reason}")
                if first_audio_at is not None:
                    print(f"      首响 {first_audio_at:.2f}s / 总耗时 {time.time() - t0:.2f}s")

    if args.once:
        handle(args.once)
        return 0

    print("\n输入 exit 退出。")
    while True:
        try:
            text = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("exit", "quit", "q", "/exit"):
            break
        try:
            handle(text)
        except KeyboardInterrupt:
            print("\n（打断）")
            continue
        except Exception as e:
            print(f"[崩溃] {e!r}")

    print("\n—— 侍奉部活动结束 ——")
    print(f"好感度 {pipeline.session.affinity}  累计 {pipeline.session.total_turns} 轮")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
