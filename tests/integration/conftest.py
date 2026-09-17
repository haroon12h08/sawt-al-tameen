from datetime import UTC, date, datetime, timedelta

import pytest

from preauth.application.services import build_services
from preauth.seed.catalogue import load_catalogue

TODAY = date.today()


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
    """Deterministic time, but today's date: the catalogue's policy periods track the current year."""
    return FixedClock(datetime.combine(TODAY, datetime.min.time(), tzinfo=UTC) + timedelta(hours=9))


@pytest.fixture
def seeded_session_factory(session_factory):
    """The catalogue from knowledge_base/, loaded exactly as a deployment loads it."""
    with session_factory() as session:
        load_catalogue(session)
        session.commit()
    return session_factory


@pytest.fixture
def services(seeded_session_factory, clock):
    return build_services(seeded_session_factory, clock=clock)
