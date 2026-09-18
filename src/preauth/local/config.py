"""Configuration for local mode, read once from the environment.

Every provider and model is configurable; nothing is hard-coded to one model. Defaults name software that is
free to install and free to run, and the diagnostics in ``preauth.local.diagnostics`` report exactly which of
them are present on this machine.
"""

import os
from dataclasses import dataclass
from enum import StrEnum


class LlmProvider(StrEnum):
    OLLAMA = "ollama"


class SttProvider(StrEnum):
    FASTER_WHISPER = "faster-whisper"
    DISABLED = "none"


class TtsProvider(StrEnum):
    PIPER = "piper"
    DISABLED = "none"


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


def _optional(name: str) -> str | None:
    return (os.environ.get(name) or "").strip() or None


def _int(name: str, default: int) -> int:
    raw = _optional(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer (got {raw!r})") from None


def _choice[T: StrEnum](enum: type[T], name: str, default: T) -> T:
    raw = _env(name, default.value).lower()
    try:
        return enum(raw)
    except ValueError:
        allowed = ", ".join(m.value for m in enum)
        raise ValueError(f"{name} must be one of: {allowed} (got {raw!r})") from None


@dataclass(frozen=True)
class LocalSettings:
    llm_provider: LlmProvider = LlmProvider.OLLAMA
    llm_model: str = "qwen2.5-coder:7b"
    llm_base_url: str = "http://localhost:11434"
    llm_timeout_seconds: int = 120
    # Low but not zero: the agent phrases sentences, it never invents facts (those come from tool results).
    llm_temperature: float = 0.1
    # Ollama defaults to a 4096-token window, which the system prompt and three tool schemas very nearly fill.
    # Left at the default, the oldest tokens -- the safety instructions -- are the ones silently dropped, so this
    # is set explicitly rather than inherited.
    llm_context_tokens: int = 8192
    # How many times one caller turn may go model -> tool -> model before the agent must speak.
    max_tool_iterations: int = 4

    stt_provider: SttProvider = SttProvider.FASTER_WHISPER
    stt_model: str = "small"
    stt_compute_type: str = "int8"
    # Directory holding an already-downloaded Whisper model, for a machine with no Internet access.
    stt_model_dir: str | None = None

    tts_provider: TtsProvider = TtsProvider.PIPER
    # Path to a Piper .onnx voice (its .onnx.json config must sit beside it).
    tts_voice_path: str | None = None

    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> "LocalSettings":
        return cls(
            llm_provider=_choice(LlmProvider, "PREAUTH_LOCAL_LLM_PROVIDER", LlmProvider.OLLAMA),
            llm_model=_env("PREAUTH_LOCAL_LLM_MODEL", cls.llm_model),
            llm_base_url=_env("PREAUTH_LOCAL_LLM_BASE_URL", cls.llm_base_url).rstrip("/"),
            llm_timeout_seconds=_int("PREAUTH_LOCAL_LLM_TIMEOUT_SECONDS", cls.llm_timeout_seconds),
            llm_temperature=float(_env("PREAUTH_LOCAL_LLM_TEMPERATURE", str(cls.llm_temperature))),
            llm_context_tokens=_int("PREAUTH_LOCAL_LLM_CONTEXT_TOKENS", cls.llm_context_tokens),
            max_tool_iterations=_int("PREAUTH_LOCAL_MAX_TOOL_ITERATIONS", cls.max_tool_iterations),
            stt_provider=_choice(SttProvider, "PREAUTH_LOCAL_STT_PROVIDER", SttProvider.FASTER_WHISPER),
            stt_model=_env("PREAUTH_LOCAL_STT_MODEL", cls.stt_model),
            stt_compute_type=_env("PREAUTH_LOCAL_STT_COMPUTE_TYPE", cls.stt_compute_type),
            stt_model_dir=_optional("PREAUTH_LOCAL_STT_MODEL_DIR"),
            tts_provider=_choice(TtsProvider, "PREAUTH_LOCAL_TTS_PROVIDER", TtsProvider.PIPER),
            tts_voice_path=_optional("PREAUTH_LOCAL_TTS_VOICE"),
            host=_env("PREAUTH_LOCAL_HOST", cls.host),
            port=_int("PREAUTH_LOCAL_PORT", cls.port),
        )

    @property
    def speech_input_enabled(self) -> bool:
        return self.stt_provider is not SttProvider.DISABLED

    @property
    def speech_output_enabled(self) -> bool:
        return self.tts_provider is not TtsProvider.DISABLED
