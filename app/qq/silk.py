"""WAV -> SILK 转换（QQ 语音消息要求的格式）。

QQ 发语音消息必须用 SILK v3 编码。SILK 是 C 写的编解码器，没有纯 Python 实现，
所以这里做成「多后端探测」：

  1. pysilk   —— 有预编译轮子时最省事
  2. pilk     —— 另一个常见绑定
  3. silk_v3_encoder(.exe) —— 官方参考实现，可用 QQ_SILK_ENCODER 环境变量指定路径

任何一个都不可用时，encode_wav_to_silk() 返回 None，调用方（bot）就只回文字，
不让「语音发不出去」把整条消息搞失败。

采样率：SILK 用 24000 Hz 单声道 16 位。我们的 TTS 输出是 32000 Hz，
用 stdlib 的 audioop.ratecv 重采样，不引第三方库。
"""

from __future__ import annotations

import audioop
import io
import logging
import os
import shutil
import subprocess
import tempfile
import wave

log = logging.getLogger(__name__)

SILK_RATE = 24000


def wav_to_pcm(wav_bytes: bytes, *, target_rate: int = SILK_RATE) -> bytes:
    """把 WAV 解成 16 位单声道 PCM，并重采样到 target_rate。"""
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        nch = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())

    if width != 2:
        raise ValueError(f"只支持 16 位 PCM，收到 {width * 8} 位")
    if nch == 2:
        frames = audioop.tomono(frames, 2, 0.5, 0.5)
    elif nch != 1:
        raise ValueError(f"不支持的声道数：{nch}")
    if rate != target_rate:
        frames, _ = audioop.ratecv(frames, 2, 1, rate, target_rate, None)
    return frames


def _encode_pysilk(pcm: bytes) -> bytes | None:
    try:
        import pysilk  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        out = pysilk.encode(pcm, sample_rate=SILK_RATE, bit_rate=24000)
        if isinstance(out, (bytes, bytearray)) and out:
            return bytes(out)
    except Exception as exc:  # noqa: BLE001
        log.debug("pysilk 编码失败：%r", exc)
    return None


def _encode_pilk(pcm: bytes) -> bytes | None:
    """pilk 是**文件式** API：pilk.encode(pcm_path, silk_path, pcm_rate)。

    注意它返回的数字（实测为 3）不是状态码也别当错误判断，
    唯一可靠的判据是产出文件是否存在且非空、开头是 #!SILK_V3。
    """
    try:
        import pilk  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    tmpdir = tempfile.mkdtemp(prefix="yukino-pilk-")
    pcm_path = os.path.join(tmpdir, "in.pcm")
    silk_path = os.path.join(tmpdir, "out.silk")
    try:
        with open(pcm_path, "wb") as f:
            f.write(pcm)
        pilk.encode(pcm_path, silk_path, SILK_RATE)
        if not os.path.isfile(silk_path):
            return None
        with open(silk_path, "rb") as f:
            data = f.read()
        if data and data[:9] == b"#!SILK_V3":
            return data
        if data:
            log.debug("pilk 输出没有 SILK 头，长度 %s", len(data))
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("pilk 编码失败：%r", exc)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _find_encoder() -> str | None:
    env = os.environ.get("QQ_SILK_ENCODER", "").strip()
    if env and os.path.isfile(env):
        return env
    for name in ("silk_v3_encoder", "silk_v3_encoder.exe"):
        hit = shutil.which(name)
        if hit:
            return hit
    return None


def _encode_cli(pcm: bytes) -> bytes | None:
    exe = _find_encoder()
    if not exe:
        return None
    tmpdir = tempfile.mkdtemp(prefix="yukino-silk-")
    pcm_path = os.path.join(tmpdir, "in.pcm")
    silk_path = os.path.join(tmpdir, "out.silk")
    try:
        with open(pcm_path, "wb") as f:
            f.write(pcm)
        # 官方 silk_v3_encoder 用法：-Fs_API 24000 -packetloss 0 in.pcm out.silk
        cmd = [exe, "-Fs_API", str(SILK_RATE), "-packetloss", "0", pcm_path, silk_path]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0:
            log.debug("silk_v3_encoder 返回 %s: %s", proc.returncode, proc.stderr[:200])
            return None
        if os.path.isfile(silk_path):
            with open(silk_path, "rb") as f:
                data = f.read()
            return data or None
        return None
    except Exception as exc:  # noqa: BLE001
        log.debug("CLI 编码失败：%r", exc)
        return None
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def available_backends() -> list[str]:
    names = []
    try:
        import pysilk  # noqa: F401

        names.append("pysilk")
    except Exception:  # noqa: BLE001
        pass
    try:
        import pilk  # noqa: F401

        names.append("pilk")
    except Exception:  # noqa: BLE001
        pass
    if _find_encoder():
        names.append("cli")
    return names


def to_tencent(data: bytes) -> bytes:
    """标准 SILK -> 腾讯（QQ/微信）容器。

    实测结论（这一条踩了很久）：
      · 标准容器 `#!SILK_V3` + frames            -> QQ 返回 850019「富媒体文件格式不支持」
      · `\\x02` + frames（丢掉魔数）              -> QQ 返回 40093008
      · **`\\x02` + `#!SILK_V3` + frames**        -> ✅ 通过，返回 file_info
    也就是腾讯的头是 10 字节：一个 0x02 前缀 + 9 字节魔法。
    pilk 源码里 get_duration 读首字节判断 0x02 后 seek(10)，正是这么回事。
    """
    if data.startswith(b"#!SILK_V3"):
        return b"\x02" + data
    return data


def encode_wav_to_silk(wav_bytes: bytes, *, tencent: bool = True) -> bytes | None:
    """WAV 字节 -> SILK 字节；没有可用编码器时返回 None。

    tencent=True（默认）输出 QQ/微信能直接吃的 0x02 前缀容器。
    """
    if not wav_bytes:
        return None
    try:
        pcm = wav_to_pcm(wav_bytes)
    except Exception as exc:  # noqa: BLE001
        log.warning("WAV 解析失败，无法转 SILK：%r", exc)
        return None
    for encoder in (_encode_pysilk, _encode_pilk, _encode_cli):
        data = encoder(pcm)
        if data:
            log.debug("SILK 编码成功（%s，%s 字节）", encoder.__name__, len(data))
            return to_tencent(data) if tencent else data
    log.info("没有可用的 SILK 编码器，本次只发文字（见 app/qq/silk.py 的说明）")
    return None
