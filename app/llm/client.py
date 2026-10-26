"""LLM 客户端 —— 纯标准库（urllib + SSE），不依赖 openai SDK。

为什么自己写：
  · 少一个重依赖，包装不上也能跑
  · 流式解析完全可控，首包延迟不被 SDK 缓冲吃掉
  · DeepSeek / OpenAI / ollama 都是 OpenAI 兼容协议，同一份代码通吃

内置 MockClient：没有网络（或没有 Key）时也能完整走通「解析 → 好感度 → UI」链路，
方便离线自测和写单元测试。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator


class LLMError(RuntimeError):
    pass


@dataclass
class ChatClient:
    api_key: str
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.8
    max_tokens: int = 1024
    timeout: float = 120.0
    max_retries: int = 2
    extra_body: dict = field(default_factory=dict, repr=False)

    # ---------- 内部 ----------
    def _endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def _payload(self, messages: list[dict], stream: bool) -> dict:
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": stream,
        }
        body.update(self.extra_body)
        return body

    def _request(self, messages: list[dict], stream: bool):
        data = json.dumps(self._payload(messages, stream), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self._endpoint(),
            data=data,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "text/event-stream" if stream else "application/json",
                "User-Agent": "yukino-app/2.0",
            },
            method="POST",
        )
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return urllib.request.urlopen(req, timeout=self.timeout)
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:800]
                except Exception:
                    pass
                # 4xx 不重试（Key 错、余额不足、参数错重试也没用）
                if 400 <= e.code < 500:
                    raise LLMError(self._explain(e.code, body)) from e
                last_err = LLMError(f"LLM 服务 {e.code}：{body}")
            except urllib.error.URLError as e:
                last_err = LLMError(f"连不上 LLM 服务 {self.base_url}（{e.reason}）")
            if attempt < self.max_retries:
                time.sleep(1.5 * (attempt + 1))
        raise last_err or LLMError("LLM 请求失败")

    @staticmethod
    def _explain(code: int, body: str) -> str:
        hint = {
            401: "API Key 无效或已吊销 —— 去 https://platform.deepseek.com/api_keys 重新生成",
            402: "账户余额不足",
            429: "请求过于频繁，触发限流",
            400: "请求参数错误",
        }.get(code, "")
        msg = f"LLM 服务返回 {code}"
        if hint:
            msg += f"（{hint}）"
        return f"{msg}：{body}"

    # ---------- 对外 ----------
    def complete(self, messages: list[dict]) -> str:
        """一次性拿到完整回复。"""
        try:
            with self._request(messages, stream=False) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:  # urlopen 已在 _request 里处理，保险起见
            raise LLMError(str(e)) from e
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception as e:
            raise LLMError(f"无法解析 LLM 响应：{raw[:300]!r}") from e
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            raise LLMError(f"响应结构异常：{data}") from e

    def stream(self, messages: list[dict]) -> Iterator[str]:
        """流式拿到增量文本。"""
        with self._request(messages, stream=True) as r:
            for raw_line in r:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield piece


class OllamaClient:
    """本地 ollama 客户端（原生 /api/chat，不是 OpenAI 兼容层）。

    为什么用原生接口：只有原生 API 才支持 `keep_alive` 和 `num_ctx`。
    在 8 GB 显存的机器上这两个参数直接决定「能不能和 GPT-SoVITS 共存」：
      · keep_alive="5m"  —— 5 分钟后自动卸载，让 TTS 拿回显存
      · keep_alive="0"   —— 每轮用完立刻卸载（省显存，代价是下轮重新加载）
    实测（RTX 4060 Laptop 8G / qwen2:7b Q4）：
      · TTS 常驻时  8 tok/s   ← 显存被 GPT-SoVITS 占满，驱动把溢出层丢到内存
      · TTS 停止时  38 tok/s  ← 差了 5 倍
    """

    provider = "ollama"

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        model: str = "qwen2:7b",
        temperature: float = 0.8,
        max_tokens: int = 240,
        num_ctx: int = 4096,
        keep_alive: str = "5m",
        timeout: float = 300.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.timeout = timeout

    # ---------- 内部 ----------
    def _post(self, messages: list[dict], stream: bool):
        body = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        req = urllib.request.Request(
            self.host + "/api/chat",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            body_txt = ""
            try:
                body_txt = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            raise LLMError(f"ollama 返回 {e.code}：{body_txt}") from e
        except urllib.error.URLError as e:
            raise LLMError(
                f"连不上本地 ollama（{self.host}，{e.reason}）。\n"
                "  1) 启动它： ollama serve\n"
                "  2) 确认模型： ollama list   （当前配置 " + self.model + "）\n"
                "  3) 想临时改用云端 API：把 .env 里 LLM_PROVIDER 改成 deepseek"
            ) from e

    # ---------- 对外 ----------
    def available(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=timeout) as r:
                return r.status == 200
        except Exception:
            return False

    def installed_models(self) -> list[str]:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=5) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            return [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            return []

    def complete(self, messages: list[dict]) -> str:
        with self._post(messages, stream=False) as r:
            raw = r.read()
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError as e:
            raise LLMError(f"ollama 响应不是 JSON：{raw[:300]!r}") from e
        return obj.get("message", {}).get("content", "") or ""

    def stream(self, messages: list[dict]) -> Iterator[str]:
        with self._post(messages, stream=True) as r:
            for raw_line in r:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = obj.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if obj.get("done"):
                    break

    def unload(self) -> None:
        """立刻把模型从显存里卸载（把 keep_alive 设为 0 发一个空请求）。"""
        body = {"model": self.model, "messages": [], "keep_alive": 0, "stream": False}
        req = urllib.request.Request(
            self.host + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=30).close()
        except Exception:
            pass

    def to_dict(self) -> dict:
        return {
            "provider": "ollama",
            "host": self.host,
            "model": self.model,
            "keep_alive": self.keep_alive,
            "num_ctx": self.num_ctx,
            "max_tokens": self.max_tokens,
        }


class MockClient:
    """离线假模型：按标签协议产出固定回复，用于无网络自测与单元测试。"""

    model = "mock"

    def __init__(self, scripts: list[dict] | None = None) -> None:
        self.scripts = scripts or [
            {
                "ja": "……ふうん。あなたにしては、まともなことを言うのね。",
                "zh": "……哼。就你而言，算是说了句像样的话。",
                "act": "合上书，抬眼看了你一下",
                "mood": "tsundere",
                "aff": 2,
            },
            {
                "ja": "別に。私はただ、そう思っただけよ。",
                "zh": "没什么。我只是这么觉得而已。",
                "act": "转过脸去",
                "mood": "shy",
                "aff": 1,
            },
            {
                "ja": "そんなこと言われても、困るのだけれど。",
                "zh": "你这么说，我会很困扰的。",
                "act": "皱眉",
                "mood": "angry",
                "aff": -2,
            },
        ]
        self._i = 0

    def _next(self) -> str:
        s = self.scripts[self._i % len(self.scripts)]
        self._i += 1
        return (
            f"<ja>{s['ja']}</ja>\n"
            f"<zh>{s['zh']}</zh>\n"
            f"<act>{s['act']}</act>\n"
            f"<mood>{s['mood']}</mood>\n"
            f"<aff>{s['aff']}</aff>"
        )

    def complete(self, messages: list[dict]) -> str:
        return self._next()

    def stream(self, messages: list[dict]) -> Iterator[str]:
        text = self._next()
        # 按 3 字切块，模拟真实的细粒度流式
        for i in range(0, len(text), 3):
            yield text[i: i + 3]


def build_client(settings):
    """按 LLM_PROVIDER 构造客户端。

    默认走本地 ollama；连不上且有 DeepSeek Key 时自动降级到云端，
    两边都没有就退回 Mock（保证界面仍然能用、能跑通链路）。
    """
    if settings.extra.get("mock_llm"):
        return MockClient()

    provider = (getattr(settings, "llm_provider", "") or "ollama").lower()

    if provider == "ollama":
        local = OllamaClient(
            host=settings.ollama_host,
            model=settings.ollama_model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            num_ctx=settings.num_ctx,
            keep_alive=settings.ollama_keep_alive,
        )
        if local.available():
            return local
        if settings.api_key:
            import logging

            logging.getLogger(__name__).warning(
                "本地 ollama 不可达，已自动降级到云端 %s", settings.model
            )
            return ChatClient(
                api_key=settings.api_key,
                base_url=settings.base_url,
                model=settings.model,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
            )
        return local  # 保留它，让错误信息直接指向「先启动 ollama」

    if provider in ("deepseek", "openai", "api"):
        if not settings.api_key:
            return MockClient()
        return ChatClient(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
        )

    return MockClient()
