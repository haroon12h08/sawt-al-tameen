"""Local tool-calling model.

The model's only job is orchestration: decide what to ask the caller next, decide which of the three tools to
call, and put the tool's answer into words. It is never a source of insurance facts. Everything it says about
cover, limits, documents or outcomes has to have come back from a tool call in the same turn.

Only genuinely local providers belong here. There is deliberately no remote-API branch to fall back to.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from preauth.local.config import LlmProvider, LocalSettings
from preauth.local.errors import LocalDependencyMissingError, LocalServiceUnavailableError

logger = logging.getLogger("preauth.local.llm")

# Reasoning models (qwen3 among them) emit their scratchpad inline; it must never reach the caller.
_THINK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
# Some Ollama models are not wired to the native tool-calling template and print the call as JSON instead.
_FENCE = re.compile(r"```(?:json|tool_call)?\s*(.+?)```", re.DOTALL)


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LlmReply:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()


class ChatModel(Protocol):
    """A local chat model that can request tool calls."""

    @property
    def name(self) -> str: ...

    def check(self) -> None:
        """Raise a local-mode error describing exactly what to start, install or pull. Return if usable."""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply: ...


def _strip_reasoning(text: str) -> str:
    return _THINK.sub("", text or "").strip()


def _as_tool_call(candidate: Any) -> "ToolCall | None":
    if not isinstance(candidate, dict):
        return None
    name = candidate.get("name") or ((candidate.get("function") or {}) if isinstance(candidate.get("function"), dict) else {}).get("name")
    if not isinstance(name, str) or not name:
        return None
    raw = candidate.get("arguments")
    if raw is None and isinstance(candidate.get("function"), dict):
        raw = candidate["function"].get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return ToolCall(name=name, arguments=raw if isinstance(raw, dict) else {})


def _outermost_json(text: str) -> str | None:
    """The widest JSON object or array in the text, whichever opens first."""
    opens = [(text.find(o), o, c) for o, c in (("[", "]"), ("{", "}")) if o in text]
    start, _, closing = min((i, o, c) for i, o, c in opens if i >= 0)
    end = text.rfind(closing)
    return text[start : end + 1] if end > start else None


def tool_calls_in_text(text: str) -> tuple[ToolCall, ...]:
    """Recover tool calls a model printed as JSON instead of returning through Ollama's tool-call field.

    Not every model packaged for Ollama is wired to the native tool-calling template; several answer with the
    call written out as a JSON object. Refusing those would make local mode work with a much narrower set of
    models for no safety gain: the recovered call goes through exactly the same toolbox validation, and an
    invented tool name comes back as TOOL_NOT_FOUND like any other.
    """
    if not text or "{" not in text:
        return ()
    fenced = _FENCE.search(text)
    candidate_text = fenced.group(1).strip() if fenced else _outermost_json(text)
    if candidate_text is None:
        return ()
    try:
        parsed = json.loads(candidate_text)
    except (json.JSONDecodeError, ValueError):
        return ()
    items = parsed if isinstance(parsed, list) else [parsed]
    calls = tuple(c for c in (_as_tool_call(i) for i in items) if c is not None)
    if calls:
        logger.info("tool_calls_recovered_from_text", extra={"tools": [c.name for c in calls]})
    return calls


def _arguments_of(call: dict[str, Any]) -> dict[str, Any]:
    """Ollama returns arguments as an object; some models emit a JSON string instead."""
    raw = (call.get("function") or {}).get("arguments", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("tool_arguments_not_json", extra={"raw": raw[:200]})
            return {}
    return raw if isinstance(raw, dict) else {}


@dataclass
class OllamaChatModel:
    """Ollama's /api/chat, used with native tool calling.

    Ollama runs entirely on this machine and needs no account, key or network once the model is pulled.
    """

    settings: LocalSettings
    _client: Any = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return f"ollama:{self.settings.llm_model}"

    def _httpx(self):
        try:
            import httpx
        except ModuleNotFoundError as e:  # pragma: no cover - exercised by the diagnostics, not the suite
            raise LocalDependencyMissingError(
                "httpx is required to talk to Ollama",
                details={"install": "uv sync --extra local"},
            ) from e
        return httpx

    def _http(self):
        if self._client is None:
            self._client = self._httpx().Client(
                base_url=self.settings.llm_base_url, timeout=self.settings.llm_timeout_seconds
            )
        return self._client

    def installed_models(self) -> list[str]:
        httpx = self._httpx()
        try:
            response = self._http().get("/api/tags", timeout=10)
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise LocalServiceUnavailableError(
                f"Ollama is not reachable at {self.settings.llm_base_url}",
                details={"start_it": "ollama serve", "reason": str(e)[:200]},
            ) from e
        return [m.get("name", "") for m in response.json().get("models", [])]

    def check(self) -> None:
        available = self.installed_models()
        wanted = self.settings.llm_model
        if not any(m == wanted or m.split(":")[0] == wanted.split(":")[0] for m in available):
            raise LocalServiceUnavailableError(
                f"Ollama is running but the model {wanted!r} is not pulled",
                details={"pull_it": f"ollama pull {wanted}", "available": available},
            )

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LlmReply:
        httpx = self._httpx()
        payload = {
            "model": self.settings.llm_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.settings.llm_temperature,
                "num_ctx": self.settings.llm_context_tokens,
            },
        }
        if tools:
            payload["tools"] = tools
        try:
            response = self._http().post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as e:
            raise LocalServiceUnavailableError(
                f"The local model did not answer within {self.settings.llm_timeout_seconds}s",
                details={"model": self.settings.llm_model, "raise_it": "PREAUTH_LOCAL_LLM_TIMEOUT_SECONDS"},
            ) from e
        except httpx.HTTPError as e:
            raise LocalServiceUnavailableError(
                f"Ollama request failed at {self.settings.llm_base_url}",
                details={"reason": str(e)[:200], "start_it": "ollama serve"},
            ) from e

        message = response.json().get("message") or {}
        calls = tuple(
            ToolCall(name=(c.get("function") or {}).get("name", ""), arguments=_arguments_of(c))
            for c in (message.get("tool_calls") or [])
        )
        text = _strip_reasoning(message.get("content", ""))
        if not calls:
            recovered = tool_calls_in_text(text)
            if recovered:
                # The "reply" was the call itself; there is nothing here to say to the caller.
                return LlmReply(text="", tool_calls=recovered)
        return LlmReply(text=text, tool_calls=calls)


def build_chat_model(settings: LocalSettings) -> ChatModel:
    if settings.llm_provider is LlmProvider.OLLAMA:
        return OllamaChatModel(settings)
    raise LocalDependencyMissingError(  # pragma: no cover - LlmProvider has one member today
        f"No local chat model is implemented for provider {settings.llm_provider.value!r}"
    )
