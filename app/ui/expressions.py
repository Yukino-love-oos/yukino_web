"""立绘表情管理。

低成本高收益的做法：先只做「表情差分」—— 每个 mood 一张图，
由协议里的 <mood> 直接驱动。等以后接 Live2D / VRM 时，只替换本模块的实现即可。

查找顺序：
    app/ui/assets/expressions/<mood>.png
    app/ui/assets/yukino.png            （通用立绘）
    yukino_brain/images/yukino_waifu.png（老路径，兼容）
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS = Path(__file__).resolve().parent / "assets"
EXPRESSIONS = ASSETS / "expressions"

FALLBACKS = [
    ASSETS / "yukino.png",
    ROOT / "yukino_brain" / "images" / "yukino_waifu.png",
]

MOOD_LABELS = {
    "calm": "平静",
    "tsundere": "别过脸",
    "soft": "柔和",
    "angry": "不悦",
    "shy": "害羞",
    "tired": "倦怠",
}


def expression_path(mood: str) -> Path | None:
    """返回该情绪对应的立绘路径；找不到就退化到通用立绘。"""
    if mood:
        p = EXPRESSIONS / f"{mood}.png"
        if p.is_file():
            return p
    for f in FALLBACKS:
        if f.is_file():
            return f
    return None


def list_expressions() -> dict[str, Path]:
    """列出当前可用的表情差分。"""
    out: dict[str, Path] = {}
    if EXPRESSIONS.is_dir():
        for p in sorted(EXPRESSIONS.glob("*.png")):
            out[p.stem] = p
    return out
