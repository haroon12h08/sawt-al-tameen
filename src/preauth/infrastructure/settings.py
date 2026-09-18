import os
from dataclasses import dataclass
from enum import StrEnum


class RuntimeMode(StrEnum):
    """Which interaction channel this process serves. The application layer below is identical for both."""

    ELEVENLABS = "elevenlabs"
    LOCAL = "local"


def normalise_database_url(url: str) -> str:
    """Hosted Postgres providers hand out postgres:// or postgresql:// URLs; use the installed psycopg 3 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./preauth.db"
    log_level: str = "INFO"
    # When set, every /api/v1 route except the voice channel requires header X-Gateway-Secret with this value.
    # Stands in for the authenticating gateway when the service is exposed on a public URL.
    gateway_secret: str | None = None
    # Bearer token the voice platform presents on server-tool calls. Voice tools are disabled when unset.
    voice_agent_token: str | None = None
    # HMAC secret of the ElevenLabs post-call webhook. The webhook endpoint is disabled when unset.
    elevenlabs_webhook_secret: str | None = None
    # Which voice channel this process exposes. ``local`` additionally mounts the local agent and its browser UI;
    # it never changes the rules, cases, review or audit layers.
    runtime_mode: RuntimeMode = RuntimeMode.ELEVENLABS

    @property
    def local_mode(self) -> bool:
        return self.runtime_mode is RuntimeMode.LOCAL

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=normalise_database_url(os.environ.get("PREAUTH_DATABASE_URL", cls.database_url)),
            log_level=os.environ.get("PREAUTH_LOG_LEVEL", cls.log_level),
            gateway_secret=_optional("PREAUTH_GATEWAY_SECRET"),
            voice_agent_token=_optional("PREAUTH_VOICE_AGENT_TOKEN"),
            elevenlabs_webhook_secret=_optional("PREAUTH_ELEVENLABS_WEBHOOK_SECRET"),
            runtime_mode=_runtime_mode(),
        )


def _runtime_mode() -> RuntimeMode:
    raw = (os.environ.get("PREAUTH_RUNTIME_MODE") or RuntimeMode.ELEVENLABS.value).strip().lower()
    try:
        return RuntimeMode(raw)
    except ValueError:
        allowed = ", ".join(m.value for m in RuntimeMode)
        raise ValueError(f"PREAUTH_RUNTIME_MODE must be one of: {allowed} (got {raw!r})") from None
