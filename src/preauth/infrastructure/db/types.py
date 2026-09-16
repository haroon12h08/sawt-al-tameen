from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

JsonType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC datetimes on every backend (SQLite stores naive values)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not accepted; use timezone-aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
