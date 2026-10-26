"""音频缓存：同一句话 + 同一套音色参数 → 直接读磁盘，零延迟。

日常寒暄（「そう」「もう」）命中率很高，是把体感延迟压下来的便宜手段。
缓存键包含全部影响音色的参数，切音色/改 speed 不会串音。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


class AudioCache:
    def __init__(self, root: str | Path, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = enabled
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def key(self, text: str, params: dict | None = None) -> str:
        blob = text + "\x1f" + json.dumps(params or {}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def path_for(self, text: str, params: dict | None = None, suffix: str = ".wav") -> Path:
        return self.root / (self.key(text, params) + suffix)

    def get(self, text: str, params: dict | None = None, suffix: str = ".wav") -> bytes | None:
        if not self.enabled:
            return None
        p = self.path_for(text, params, suffix)
        if p.is_file():
            try:
                data = p.read_bytes()
                if data:
                    return data
            except OSError:
                return None
        return None

    def put(self, text: str, params: dict | None, data: bytes, suffix: str = ".wav") -> Path | None:
        if not self.enabled or not data:
            return None
        p = self.path_for(text, params, suffix)
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(p)
            return p
        except OSError:
            return None

    def stats(self) -> dict:
        if not self.enabled or not self.root.is_dir():
            return {"enabled": False, "files": 0, "mb": 0.0}
        files = list(self.root.glob("*.wav")) + list(self.root.glob("*.ogg"))
        total = sum(f.stat().st_size for f in files if f.is_file())
        return {"enabled": True, "files": len(files), "mb": round(total / 1048576, 2)}

    def clear(self) -> int:
        n = 0
        if self.root.is_dir():
            for f in self.root.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                        n += 1
                    except OSError:
                        pass
        return n


def merge_wav(chunks: list[bytes]) -> bytes:
    """把多段同参数 WAV 拼成一段，方便一次性播放/回放。

    只处理「同采样率、同声道、同位深」的情况；参数不一致时直接返回第一段，
    避免拼出噪声。先各自补好头，再按 PCM 数据拼接，最后重算一次头。
    """
    import io
    import wave

    valid: list[bytes] = []
    for c in chunks:
        if c:
            fixed = fix_wav_header(c)
            if fixed[:4] == b"RIFF" and fixed[8:12] == b"WAVE":
                valid.append(fixed)
    if not valid:
        return b""
    if len(valid) == 1:
        return valid[0]

    params = None
    pcm_parts: list[bytes] = []
    for data in valid:
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                cur = (w.getnchannels(), w.getsampwidth(), w.getframerate())
                if params is None:
                    params = cur
                elif cur != params:
                    return valid[0]
                pcm_parts.append(w.readframes(w.getnframes()))
        except Exception:
            return valid[0]
    if params is None:
        return valid[0]

    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        for part in pcm_parts:
            out.writeframes(part)
    return buf.getvalue()


def merge_wav_count(chunks: list[bytes]) -> int:
    """拼接时跳过的无效段数（用于日志）。"""
    return sum(1 for c in chunks if not c)


def fix_wav_header(data: bytes) -> bytes:
    """修补流式拼接出来的 WAV 头。

    api_v2 在 streaming_mode 下是「边合成边吐字节」，写头的时候还不知道总长度，
    于是 RIFF 大小和 data 大小为 0 —— 播放器会认为没有音频。
    这里按实际字节数把两个长度字段改对，其他内容一律不动。
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data

    buf = bytearray(data)
    pos = 12
    guard = 0
    while pos + 8 <= len(buf) and guard < 64:
        guard += 1
        chunk_id = bytes(buf[pos: pos + 4])
        size = int.from_bytes(buf[pos + 4: pos + 8], "little")
        if chunk_id == b"data":
            actual = len(buf) - (pos + 8)
            buf[pos + 4: pos + 8] = actual.to_bytes(4, "little")
            buf[4: 8] = (len(buf) - 8).to_bytes(4, "little")
            return bytes(buf)
        # size 可能是 0 / 0xFFFFFFFF（流式写入时的占位），当作未知处理
        if size in (0, 0xFFFFFFFF) or pos + 8 + size > len(buf):
            return data
        pos += 8 + size + (size & 1)
    return data


def audio_report(data: bytes) -> dict:
    """检查返回的音频是否像正常语音（不依赖第三方库）。"""
    import io
    import struct
    import wave

    info = {"bytes": len(data), "format": "unknown"}
    if len(data) < 16:
        return info
    if data[:4] == b"OggS":
        info["format"] = "ogg"
        return info
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                n = w.getnframes()
                sr = w.getframerate()
                info.update(
                    format="wav",
                    channels=w.getnchannels(),
                    sample_rate=sr,
                    sample_width=w.getsampwidth(),
                    frames=n,
                    duration_s=round(n / sr, 2) if sr else 0,
                )
            # 粗略音量：16bit 采样的峰值与平均绝对值
            with wave.open(io.BytesIO(data), "rb") as w:
                frames = w.readframes(w.getnframes())
            if info.get("sample_width") == 2 and frames:
                samples = struct.unpack(f"<{len(frames) // 2}h", frames)
                peak = max(abs(s) for s in samples)
                mean = sum(abs(s) for s in samples) / len(samples)
                info["peak"] = peak
                info["mean_abs"] = round(mean, 1)
                info["silent"] = peak < 500
                # 静音占比（连续 0 采样视为静音）
                run = best = 0
                for s in samples:
                    if s == 0:
                        run += 1
                        best = max(best, run)
                    else:
                        run = 0
                info["longest_silence_ms"] = round(best / (sr or 1) * 1000, 1)
        except Exception as e:
            info["error"] = repr(e)
    return info
