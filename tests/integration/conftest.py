from datetime import UTC, date, datetime, timedelta

import pytest

from preauth.application.services import build_services
from preauth.seed.reference_data import load_reference_data

TODAY = date(2026, 9, 16)


class FixedClock:
    """Deterministic clock that advances one millisecond per reading so orderings stay strict."""

    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        self._now += timedelta(milliseconds=1)
        return self._now

    def today(self) -> date:
        return self._now.date()


@pytest.fixture
def clock():
    return FixedClock(datetime(2026, 9, 16, 9, 0, tzinfo=UTC))


@pytest.fixture
def seeded_session_factory(session_factory):
    with session_factory() as session:
        load_reference_data(session, TODAY)
        session.commit()
    return session_factory


@pytest.fixture
def services(seeded_session_factory, clock):
    return build_services(seeded_session_factory, clock=clock)
