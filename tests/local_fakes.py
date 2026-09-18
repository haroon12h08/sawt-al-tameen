"""Test doubles for local mode.

CI has no Ollama, no Whisper weights and no Piper voice, and downloading an 8B model to run a test suite would
be absurd. These fakes stand in for the three local engines. They are deliberately dumb: they replay a script,
so what the tests actually exercise is the agent loop, the tool boundary and the guardrails, not a model.
"""

from dataclasses import dataclass, field
from typing import Any

from preauth.local.errors import LocalServiceUnavailableError
from preauth.local.llm import LlmReply, ToolCall
from preauth.local.speech import Speech, Transcription


@dataclass
class ScriptedChatModel:
    """Replays a list of replies, recording the messages and tools it was given."""

    replies: list[LlmReply] = field(default_factory=list)
    seen: list[list[dict[str, Any]]] = field(default_factory=list)
    tools_offered: list[list[dict[str, Any]]] = field(default_factory=list)
    healthy: bool = True

    @property
    def name(self) -> str:
        return "scripted"

    def check(self) -> None:
        if not self.healthy:
            raise LocalServiceUnavailableError("Ollama is not reachable", details={"start_it": "ollama serve"})

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply:
        self.seen.append(messages)
        self.tools_offered.append(tools)
        if not self.replies:
            return LlmReply(text="Is there anything else within scope?")
        return self.replies.pop(0)


def says(text: str) -> LlmReply:
    return LlmReply(text=text)


def calls(name: str, **arguments: Any) -> LlmReply:
    return LlmReply(text="", tool_calls=(ToolCall(name=name, arguments=arguments),))


@dataclass
class FakeTranscriber:
    text: str = "hello"
    language: str | None = "en"
    calls: list[bytes] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake-stt"

    @property
    def enabled(self) -> bool:
        return True

    def check(self) -> None:
        return None

    def transcribe(self, audio: bytes, language: str | None = None) -> Transcription:
        self.calls.append(audio)
        return Transcription(text=self.text, language=language or self.language, duration_seconds=1.0)


@dataclass
class FakeSynthesizer:
    spoken: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "fake-tts"

    @property
    def enabled(self) -> bool:
        return True

    def check(self) -> None:
        return None

    def synthesize(self, text: str) -> Speech:
        self.spoken.append(text)
        return Speech(audio=b"RIFF....WAVEfake", media_type="audio/wav")
