"""GPT-SoVITS api_v2 客户端 —— 纯标准库（urllib）。

修正了原 yukino_web.py 的三个致命错误：
  1. 启动脚本跑的是 api.py，参数名却是 api_v2.py 的（text_lang / prompt_lang / speed_factor）
  2. 缺 ref_audio_path（api_v2 必填），导致 default_refer 未就绪 → 永远拿不到音频
  3. 传了 api_v2 根本没有的 spk 参数

正确接口（api_v2.py）::

    GET /tts?text=...&text_lang=ja&ref_audio_path=...&prompt_lang=ja&prompt_text=...
    GET /set_gpt_weights?weights_path=...
    GET /set_sovits_weights?weights_path=...
    GET /control?command=restart
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


class GsvError(RuntimeError):
    """TTS 服务调用失败。"""

@dataclass
class GsvClient:
    base: str = "http://127.0.0.1:9880"
    ref_audio: Path = Path(".")
    prompt_text: str = ""
    prompt_lang: str = "ja"
    text_lang: str = "ja"
    timeout: float = 180.0

    # 采样参数
    top_k: int = 5
    top_p: float = 1.0
    temperature: float = 1.0
    speed_factor: float = 1.0
    repetition_penalty: float = 1.35
    text_split_method: str = "cut0"
    extra_params: dict = field(default_factory=dict, repr=False)

    # ---------- 基础 ----------
    def _url(self, path: str, params: dict | None = None) -> str:
        url = self.base.rstrip("/") + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url += "?" + urllib.parse.urlencode(clean, doseq=True)
        return url

    def _open(self, path: str, params: dict | None = None, timeout: float | None = None):
        req = urllib.request.Request(
            self._url(path, params),
            headers={"User-Agent": "yukino-app/2.0", "Accept": "*/*"},
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:600]
            except Exception:
                pass
            raise GsvError(f"TTS 服务返回 {e.code}：{body}") from e
        except urllib.error.URLError as e:
            raise GsvError(
                f"连不上 TTS 服务 {self.base}（{e.reason}）。\n"
                "请先运行： .\\scripts\\start_tts.ps1"
            ) from e

    # ---------- 健康检查 / 权重切换 ----------
    def health(self, timeout: float = 3.0) -> bool:
        try:
            with self._open("/docs", timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False

    def wait_ready(self, timeout: float = 300.0, interval: float = 3.0) -> bool:
        """等模型加载完。api_v2 起来后 /docs 可用即视为就绪。"""
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.health():
                return True
            time.sleep(interval)
        return False

    def set_gpt_weights(self, weights_path: str) -> None:
        self._get_ok("/set_gpt_weights", {"weights_path": weights_path})

    def set_sovits_weights(self, weights_path: str) -> None:
        self._get_ok("/set_sovits_weights", {"weights_path": weights_path})

    def restart(self) -> None:
        self._get_ok("/control", {"command": "restart"})

    def _get_ok(self, path: str, params: dict) -> None:
        with self._open(path, params, timeout=120) as r:
            raw = r.read()
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return
        if isinstance(data, dict) and data.get("message") not in (None, "success"):
            raise GsvError(f"{path} 失败：{data}")

    # ---------- 合成 ----------
    def _payload(self, text: str, *, streaming: bool, media_type: str, overrides: dict) -> dict:
        p = {
            "text": text,
            "text_lang": self.text_lang,
            "ref_audio_path": str(self.ref_audio).replace("\\", "/"),
            "prompt_text": self.prompt_text,
            "prompt_lang": self.prompt_lang,
            "text_split_method": self.text_split_method,
            "batch_size": 1,
            "media_type": media_type,
            "streaming_mode": "true" if streaming else "false",
            "top_k": self.top_k,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "speed_factor": self.speed_factor,
            "repetition_penalty": self.repetition_penalty,
            "seed": -1,
            "parallel_infer": "true",
            "split_bucket": "true",
        }
        p.update(self.extra_params)
        p.update(overrides)
        return p

    def synth(
        self,
        text: str,
        *,
        media_type: str = "wav",
        **overrides,
    ) -> bytes:
        """一次性合成整句，返回完整音频字节。"""
        if not text.strip():
            return b""
        params = self._payload(text, streaming=False, media_type=media_type, overrides=overrides)
        with self._open("/tts", params) as r:
            data = r.read()
        if not data:
            raise GsvError("TTS 返回空音频")
        return data

    def synth_stream(self, text: str, *, media_type: str = "ogg", **overrides) -> Iterator[bytes]:
        """分块流式合成。注意：流式模式只支持 ogg/aac，wav 不支持。"""
        if not text.strip():
            return
        params = self._payload(text, streaming=True, media_type=media_type, overrides=overrides)
        with self._open("/tts", params) as r:
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                yield chunk

    def to_dict(self) -> dict:
        return {
            "base": self.base,
            "ref_audio": str(self.ref_audio),
            "prompt_text": self.prompt_text,
            "prompt_lang": self.prompt_lang,
            "text_lang": self.text_lang,
            "top_k": self.top_k,
            "top_p": self.top_p,
            "temperature": self.temperature,
            "speed_factor": self.speed_factor,
            "repetition_penalty": self.repetition_penalty,
            "text_split_method": self.text_split_method,
        }


def from_settings(settings) -> GsvClient:
    """从 Settings 构造客户端。"""
    return GsvClient(
        base=settings.gsv_api_base,
        ref_audio=settings.gsv_ref_audio,
        prompt_text=settings.gsv_prompt_text,
        prompt_lang=settings.gsv_prompt_lang,
        text_lang="ja",
        top_k=settings.tts_top_k,
        top_p=settings.tts_top_p,
        temperature=settings.tts_temperature,
        speed_factor=settings.tts_speed,
        repetition_penalty=settings.tts_repetition_penalty,
    )
