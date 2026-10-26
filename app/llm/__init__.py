"""LLM 层：人格卡、客户端、输出协议解析。"""

from .client import ChatClient, LLMError, MockClient, OllamaClient, build_client
from .persona import Persona, load_persona, render_system_prompt
from .protocol import KNOWN_MOODS, ProtocolParser, Turn, clean_for_tts, is_speakable

__all__ = [
    "ChatClient",
    "LLMError",
    "MockClient",
    "OllamaClient",
    "build_client",
    "Persona",
    "load_persona",
    "render_system_prompt",
    "ProtocolParser",
    "Turn",
    "clean_for_tts",
    "is_speakable",
    "KNOWN_MOODS",
]
