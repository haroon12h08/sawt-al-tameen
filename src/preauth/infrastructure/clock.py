import secrets
import uuid
from datetime import UTC, date, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def today(self) -> date: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return self.now().date()


def new_id() -> str:
    return str(uuid.uuid4())


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_case_reference() -> str:
    """Short, speakable reference for voice and phone use (no I, L, O, U)."""
    return "PA-" + "".join(secrets.choice(_CROCKFORD) for _ in range(8))
