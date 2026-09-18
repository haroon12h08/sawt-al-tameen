"""Failures specific to running the stack locally.

Local mode never degrades to a paid API when something is missing. It stops and says exactly what to install,
start or download, because a silent fallback would make the "no paid API" guarantee unverifiable.
"""

from preauth.domain.errors import DomainError


class LocalModeError(DomainError):
    code = "LOCAL_MODE_ERROR"


class LocalDependencyMissingError(LocalModeError):
    """A Python package or model file needed for local mode is not installed. Carries the command to fix it."""

    code = "LOCAL_DEPENDENCY_MISSING"


class LocalServiceUnavailableError(LocalModeError):
    """A local service (Ollama) is not reachable, or does not hold the configured model."""

    code = "LOCAL_SERVICE_UNAVAILABLE"


class SpeechNotRecognisedError(LocalModeError):
    """The microphone produced no intelligible speech. The caller is asked to repeat; nothing is guessed."""

    code = "SPEECH_NOT_RECOGNISED"


class ConversationNotFoundError(LocalModeError):
    code = "CONVERSATION_NOT_FOUND"


class ConversationClosedError(LocalModeError):
    """The call has ended and its transcript is stored. Call records are immutable, so no turn can be added."""

    code = "CONVERSATION_CLOSED"


class LocalModeNotEnabledError(LocalModeError):
    code = "CHANNEL_NOT_CONFIGURED"
