"""音频后处理：压缩过长的停顿。

为什么需要
----------
GPT-SoVITS 在 CPU/v2 模型上合成日语时，会在短语之间插入 0.4~0.6 秒的停顿，
一段 3 秒的台词里静音占比能到 50%+。听感上就是「断断续续」「一顿一顿」。
实测把 SILK 解码回来和原始 WAV 逐窗对比，静音结构完全一致，
所以问题不在编码（SILK 是无损的），纯在合成输出。

这个模块只做一件事：把过长的静音段裁短到自然呼吸的长度，
顺手去掉首尾多余静音。纯 stdlib（wave + audioop），不引依赖。

参数选择
--------
· 短语间默认保留 180 ms 停顿 —— 实测听感最自然（80/120/180 三档对比后选定）。
  完全去掉会连读成一句，反而不自然；想更紧凑可把 max_gap_ms 调到 80~120。
· 结尾保留 150 ms —— 给播放器一点余量，避免最后一个音被截断
· 开头保留 30 ms —— 防止起音被削
"""

from __future__ import annotations

import audioop
import io
import wave

WIN_MS = 20


def _read_mono(wav_bytes: bytes) -> tuple[bytes, int, int]:
    """返回 (16bit 单声道 PCM, 采样率, 原始声道数)。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        nch = w.getnchannels()
        rate = w.getframerate()
        width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"只支持 16 位 PCM，收到 {width * 8} 位")
    if nch == 2:
        frames = audioop.tomono(frames, 2, 0.5, 0.5)
    return frames, rate, nch


def _write_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def squeeze_pauses(
    wav_bytes: bytes,
    *,
    max_gap_ms: int = 180,
    head_keep_ms: int = 30,
    tail_keep_ms: int = 150,
) -> bytes:
    """压缩过长停顿；失败时原样返回（绝不因为后处理把消息搞失败）。"""
    if not wav_bytes:
        return wav_bytes
    try:
        pcm, rate, nch = _read_mono(wav_bytes)
    except Exception:  # noqa: BLE001
        return wav_bytes
    if not pcm:
        return wav_bytes

    win = int(rate * WIN_MS / 1000) * 2  # 字节数
    if win <= 0 or len(pcm) < win * 3:
        return wav_bytes

    levels = []
    for i in range(0, len(pcm) - win + 1, win):
        levels.append(audioop.rms(pcm[i:i + win], 2))
    if not levels:
        return wav_bytes

    peak = max(levels) or 1
    thr = max(70, int(peak * 0.05))

    # 找出所有静音段（以窗口为单位）
    runs: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(levels):
        if v < thr:
            if start is None:
                start = i
        elif start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(levels)))

    if not runs:
        return wav_bytes  # 没有静音，无需处理

    keep_gap = max(1, int(max_gap_ms / WIN_MS))
    keep_head = max(1, int(head_keep_ms / WIN_MS))
    keep_tail = max(1, int(tail_keep_ms / WIN_MS))

    out = bytearray()
    cursor = 0  # 已写到的窗口位置
    for a, b in runs:
        n = b - a
        is_head = a == 0
        is_tail = b == len(levels)
        if is_head:
            keep = keep_head
            drop_from = a + keep
            drop_to = b if not is_tail else max(a + keep, b - keep_tail)
            # 开头静音：只留 keep_head
            out += pcm[cursor * win:(a + min(keep, n)) * win]
            cursor = b if n <= keep else a + keep
        elif is_tail:
            keep = keep_tail
            if n <= keep:
                continue  # 尾静音本来就短，不动
            # 尾巴：保留最后 keep_tail
            out += pcm[cursor * win:a * win]
            out += pcm[(b - keep) * win:b * win]
            cursor = b
            continue
        else:
            if n <= keep_gap:
                continue  # 正常停顿，保留
            out += pcm[cursor * win:(a + keep_gap) * win]
            cursor = b
        # 处理完该静音段后继续
        if is_head and n > keep:
            cursor = a + keep
    # 收尾：把剩余部分接上
    if cursor < len(levels):
        out += pcm[cursor * win:]
    else:
        # cursor 可能已经覆盖到最后；补上尾部未处理的数据
        pass

    if not out:
        return wav_bytes
    return _write_wav(bytes(out), rate)


def pause_stats(wav_bytes: bytes) -> dict:
    """调试用：返回时长/静音占比/长停顿数量。"""
    try:
        pcm, rate, _ = _read_mono(wav_bytes)
    except Exception:  # noqa: BLE001
        return {}
    win = int(rate * WIN_MS / 1000) * 2
    if win <= 0 or not pcm:
        return {}
    levels = [audioop.rms(pcm[i:i + win], 2) for i in range(0, len(pcm) - win + 1, win)]
    if not levels:
        return {}
    thr = max(70, int((max(levels) or 1) * 0.05))
    silent = sum(1 for v in levels if v < thr)
    runs, start = [], None
    for i, v in enumerate(levels):
        if v < thr and start is None:
            start = i
        elif v >= thr and start is not None:
            runs.append(i - start)
            start = None
    if start is not None:
        runs.append(len(levels) - start)
    return {
        "duration": (len(pcm) // 2) / float(rate),
        "silent_ratio": silent / len(levels),
        "long_gaps": sum(1 for n in runs if n * WIN_MS >= 150),
    }
