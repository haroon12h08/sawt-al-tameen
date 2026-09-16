import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("PREAUTH_DATABASE_URL", "sqlite:///./preauth.db"),
            log_level=os.environ.get("PREAUTH_LOG_LEVEL", "INFO"),
        )
