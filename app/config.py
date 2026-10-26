"""配置加载 —— 纯标准库实现。

优先级：真实环境变量  >  .env 文件  >  默认值。
不引入 pydantic-settings，理由见 app/__init__.py。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n", ""}


def parse_dotenv(path: Path) -> dict[str, str]:
    """解析 .env，支持 # 注释、export 前缀、单双引号。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def _as_bool(raw: str, default: bool = False) -> bool:
    if raw is None:
        return default
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return default


def _as_path(raw: str, base: Path) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _resolve_persona(path: Path) -> Path:
    """人格卡容错：配置写的扩展名不对时，自动找同名 .json / .yaml。"""
    if path.is_file():
        return path
    for ext in (".json", ".yaml", ".yml"):
        cand = path.with_suffix(ext)
        if cand.is_file():
            return cand
    return path


def _resolve_ref_audio(path: Path) -> Path:
    """参考音频容错：配置的路径不存在时，自动取同目录下 001.* 开头的 wav。"""
    if path.is_file():
        return path
    parent = path.parent
    if parent.is_dir():
        for p in sorted(parent.glob("001.*")):
            if p.suffix.lower() in (".wav", ".mp3", ".flac"):
                return p
    return path


@dataclass(frozen=True)
class Settings:
    # ---- LLM ----
    # provider: ollama(本地) | deepseek | openai 兼容 | mock
    llm_provider: str = "ollama"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    temperature: float = 0.8
    max_tokens: int = 240
    num_ctx: int = 4096
    # 回放最近几轮对话（每轮 = 1 user + 1 assistant）。小模型上下文有限，
    # 加上 few-shot 后太长会明显变慢，默认 6 轮。
    history_turns: int = 6

    # ---- 本地 ollama ----
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2:7b"
    # 生成完多久释放显存。8G 卡上和 GPT-SoVITS 抢显存，建议 5m；
    # 设 0 表示用完立刻卸载（下轮要重新加载，约 2~8s，但 TTS 能拿回显存）
    ollama_keep_alive: str = "5m"

    # 本地兜底（DeepSeek 不可用时）
    fallback_base_url: str = ""
    fallback_model: str = ""

    # ---- TTS ----
    gsv_api_base: str = "http://127.0.0.1:9880"
    # ⚠ prompt_text 必须与参考音频里真实说出的话逐字一致，否则音色会漂。
    #   这个文本取自参考音频文件名（即原作者标注的转写），不是原代码里那句
    #   带「あなた、」「を」的版本——那句与音频不符。
    gsv_ref_audio: Path = ROOT / "yukino/Yukino/参考音频/001.あなたいきなり姉さんのこと言い当てるから驚いたわ.wav"
    gsv_prompt_text: str = "あなたいきなり姉さんのこと言い当てるから驚いたわ。"
    gsv_prompt_lang: str = "ja"
    gsv_gpt_weights: str = "GPT_weights/default.ckpt"
    gsv_sovits_weights: str = "SoVITS_weights/Yukino_Strong.pth"
    gsv_voice_strong: str = "SoVITS_weights/Yukino_Strong.pth"
    gsv_voice_weak: str = "SoVITS_weights/Yukino_Weak_01.pth"

    # ---- 应用 ----
    persona_path: Path = ROOT / "persona/yukino.json"
    db_path: Path = ROOT / "data/yukino.db"
    tts_cache_dir: Path = ROOT / "data/tts_cache"
    log_level: str = "INFO"

    # ---- 行为开关 ----
    llm_stream: bool = True
    # TTS 流式默认关闭：api_v2 开流式会自动关闭并行推理与分桶处理，
    # 整句总耗时反而更长（实测 2.3s → 3.0~4.0s）。
    # 当前架构是「整句收完再播」，所以非流式最快；等实现真·边收边播再打开。
    tts_stream: bool = False
    use_tts_cache: bool = True
    prefetch_first_sentence: bool = True

    # ---- TTS 采样参数 ----
    tts_top_k: int = 5
    tts_top_p: float = 1.0
    tts_temperature: float = 1.0
    tts_speed: float = 1.0
    tts_repetition_penalty: float = 1.35

    # ---- TTS 流式档位（实测数据，见 docs/改造方案.md 的性能表）----
    #   api_v2 的 streaming_mode: 1=质量最好最慢  2=折中  3=质量最低最快
    #   实测同一句话首包：mode1=3.1s  mode2=2.6s  mode3=0.40s
    #   策略：第一句用 3 抢首响，之后用 1 保音质
    tts_first_stream_mode: int = 3
    tts_stream_mode: int = 1

    extra: dict = field(default_factory=dict, repr=False)

    # ---------- 便捷属性 ----------
    @property
    def gsv_root(self) -> Path:
        return ROOT / "yukino_voice/GPT-SoVITS-v2pro-20250604"

    def require_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "缺少 DEEPSEEK_API_KEY。\n"
                "  1) 复制 .env.example 为 .env\n"
                "  2) 去 https://platform.deepseek.com/api_keys 新建 Key 并填入\n"
                "  3) 只想离线试玩可以设置 YUKINO_MOCK_LLM=1 使用内置假模型"
            )
        return self.api_key


def load_settings(dotenv: Path | None = None) -> Settings:
    """加载配置。dotenv 默认取仓库根目录 .env。"""
    file_vals = parse_dotenv(dotenv or (ROOT / ".env"))

    def get(key: str, default: str = "") -> str:
        # 环境变量优先；空字符串视为未设置
        v = os.environ.get(key)
        if v not in (None, ""):
            return v
        return file_vals.get(key, default)

    def gpath(key: str, default: Path) -> Path:
        raw = get(key)
        return _as_path(raw, ROOT) if raw else default

    return Settings(
        llm_provider=get("LLM_PROVIDER", "ollama").strip().lower(),
        api_key=get("DEEPSEEK_API_KEY"),
        base_url=get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=get("DEEPSEEK_MODEL", "deepseek-chat"),
        temperature=float(get("LLM_TEMPERATURE", "0.8")),
        max_tokens=int(get("LLM_MAX_TOKENS", "240")),
        num_ctx=int(get("LLM_NUM_CTX", "4096")),
        history_turns=int(get("LLM_HISTORY_TURNS", "6")),
        ollama_host=get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
        ollama_model=get("OLLAMA_MODEL", "qwen2:7b"),
        ollama_keep_alive=get("OLLAMA_KEEP_ALIVE", "5m"),
        fallback_base_url=get("FALLBACK_BASE_URL"),
        fallback_model=get("FALLBACK_MODEL"),
        gsv_api_base=get("GSV_API_BASE", "http://127.0.0.1:9880").rstrip("/"),
        gsv_ref_audio=_resolve_ref_audio(
            gpath(
                "GSV_REF_AUDIO",
                ROOT / "yukino/Yukino/参考音频/001.あなたいきなり姉さんのこと言い当てるから驚いたわ.wav",
            )
        ),
        gsv_prompt_text=get(
            "GSV_PROMPT_TEXT", "あなたいきなり姉さんのこと言い当てるから驚いたわ。"
        ),
        gsv_prompt_lang=get("GSV_PROMPT_LANG", "ja"),
        gsv_gpt_weights=get("GSV_GPT_WEIGHTS", "GPT_weights/default.ckpt"),
        gsv_sovits_weights=get("GSV_SOVITS_WEIGHTS", "SoVITS_weights/Yukino_Strong.pth"),
        gsv_voice_strong=get("GSV_VOICE_STRONG", "SoVITS_weights/Yukino_Strong.pth"),
        gsv_voice_weak=get("GSV_VOICE_WEAK", "SoVITS_weights/Yukino_Weak_01.pth"),
        persona_path=_resolve_persona(
            gpath("YUKINO_PERSONA", ROOT / "persona/yukino.json")
        ),
        db_path=gpath("DB_PATH", ROOT / "data/yukino.db"),
        tts_cache_dir=gpath("TTS_CACHE_DIR", ROOT / "data/tts_cache"),
        log_level=get("LOG_LEVEL", "INFO").upper(),
        llm_stream=_as_bool(get("LLM_STREAM", "true"), True),
        tts_stream=_as_bool(get("TTS_STREAM", "false"), False),
        use_tts_cache=_as_bool(get("TTS_CACHE", "true"), True),
        prefetch_first_sentence=_as_bool(get("PREFETCH_FIRST_SENTENCE", "true"), True),
        tts_top_k=int(get("TTS_TOP_K", "5")),
        tts_top_p=float(get("TTS_TOP_P", "1.0")),
        tts_temperature=float(get("TTS_TEMPERATURE", "1.0")),
        tts_speed=float(get("TTS_SPEED", "1.0")),
        tts_repetition_penalty=float(get("TTS_REPETITION_PENALTY", "1.35")),
        tts_first_stream_mode=int(get("TTS_FIRST_STREAM_MODE", "3")),
        tts_stream_mode=int(get("TTS_STREAM_MODE", "1")),
        extra={"mock_llm": get("YUKINO_MOCK_LLM", "0") not in _FALSE},
    )


if __name__ == "__main__":  # 自检： python -m app.config
    s = load_settings()
    print(f"LLM provider           = {s.llm_provider}")
    if s.llm_provider == "ollama":
        print(f"  ollama               = {s.ollama_host} / {s.ollama_model}")
        print(f"  keep_alive / num_ctx = {s.ollama_keep_alive} / {s.num_ctx}")
    else:
        print(f"  model                = {s.model}  (key {'已配置' if s.api_key else '未配置'})")
    for k in (
        "gsv_api_base", "gsv_ref_audio", "persona_path", "db_path",
        "llm_stream", "tts_stream", "use_tts_cache", "history_turns",
    ):
        print(f"{k:22} = {getattr(s, k)}")
    print(f"{'参考音频存在':22} = {s.gsv_ref_audio.is_file()}")
    print(f"{'人格卡存在':22} = {s.persona_path.is_file()}")
