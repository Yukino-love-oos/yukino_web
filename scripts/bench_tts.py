"""TTS 合成速度基准 —— 用来评估「目标机器能不能跑 CPU 推理」。

用途：把项目放到新机器（例如云 ECS）后，先跑这个脚本量一下真实速度，
再决定要不要买 GPU / 换云 TTS。

用法：
    # 先起服务（GPU 或 CPU 都行，端口对上即可）
    .\\scripts\\start_tts.ps1                       # GPU，默认 9880
    # 或 CPU 模式：
    #   runtime\\python.exe api_v2.py -a 127.0.0.1 -p 9881 -c D:\\Yukino\\configs\\tts_infer_cpu.yaml
    #   python scripts/bench_tts.py --port 9881

    python scripts/bench_tts.py                 # 默认 9880
    python scripts/bench_tts.py --port 9881     # CPU 模式

输出：每句的合成耗时、音频时长、实时倍率（<1 表示比实时慢）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.tts.cache import audio_report  # noqa: E402
from app.tts.gsv import GsvClient  # noqa: E402

TEXTS = [
    ("短句", "そう。"),
    ("中句", "それはどうかしら。"),
    ("长句", "別に、あなたのためじゃないわ。"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="TTS 合成速度基准")
    ap.add_argument("--port", type=int, default=9880)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--repeat", type=int, default=2, help="每句测几次（含预热，取最后一次）")
    args = ap.parse_args(argv)

    s = load_settings()
    c = GsvClient(
        base=f"http://{args.host}:{args.port}",
        ref_audio=s.gsv_ref_audio,
        prompt_text=s.gsv_prompt_text,
        text_lang="ja",
        prompt_lang="ja",
        timeout=900,
    )

    print("=" * 64)
    print(f"  TTS 基准  ->  http://{args.host}:{args.port}")
    print(f"  参考音频  ->  {s.gsv_ref_audio.name}")
    print("=" * 64)

    if not c.health():
        print("服务未就绪。先启动： .\\scripts\\start_tts.ps1")
        return 1

    total_audio = 0.0
    total_time = 0.0

    for name, text in TEXTS:
        times: list[float] = []
        data = b""
        for _ in range(max(1, args.repeat)):
            t0 = time.time()
            try:
                data = c.synth(text)
            except Exception as e:
                print(f"  {name}: 失败 {e}")
                break
            times.append(time.time() - t0)
        if not times:
            continue
        rep = audio_report(data)
        dur = float(rep.get("duration_s") or 0)
        warm = times[-1]
        total_audio += dur
        total_time += warm
        rt = (dur / warm) if warm else 0
        flag = "OK" if rt >= 1.0 else ("可用" if rt >= 0.3 else "偏慢")
        print(
            f"  {name} {text!r:32}\n"
            f"      耗时 {warm:6.2f}s（首次 {times[0]:5.2f}s）  "
            f"音频 {dur:4.1f}s  实时倍率 {rt:4.2f}x  [{flag}]"
        )

    if total_time:
        print("-" * 64)
        print(
            f"  合计：音频 {total_audio:.1f}s / 合成 {total_time:.1f}s "
            f"→ 平均实时倍率 {total_audio/total_time:.2f}x"
        )
        print(
            "  参考：一份 3 句回复（约 6s 音频）预计需要 "
            f"{6/ (total_audio/total_time):.1f} 秒"
        )
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
