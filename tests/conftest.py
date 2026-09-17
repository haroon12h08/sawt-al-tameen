import os
import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from preauth.infrastructure.db.models import Base
from preauth.infrastructure.db.session import build_engine, build_session_factory
from preauth.infrastructure.settings import normalise_database_url

ROOT = Path(__file__).resolve().parents[1]

# Set PREAUTH_TEST_DATABASE_URL to run the whole suite against PostgreSQL instead of SQLite (CI does both).
POSTGRES_URL = os.environ.get("PREAUTH_TEST_DATABASE_URL", "").strip()


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["database_url"] = database_url
    return cfg


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory) -> Path:
    """A SQLite database built by running the real migrations once per test session."""
    path = tmp_path_factory.mktemp("db") / "template.db"
    command.upgrade(alembic_config(f"sqlite:///{path}"), "head")
    return path


@pytest.fixture(scope="session")
def _postgres_engine() -> Engine:
    """Migrated PostgreSQL database, shared by the session; each test starts from empty tables."""
    url = normalise_database_url(POSTGRES_URL)
    engine = build_engine(url)
    with engine.begin() as conn:  # start from a known-empty schema
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    command.upgrade(alembic_config(url), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def engine(request, tmp_path) -> Engine:
    if POSTGRES_URL:
        engine = request.getfixturevalue("_postgres_engine")
        tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        with engine.begin() as conn:
            # TRUNCATE does not fire the row-level append-only triggers, so test data can be cleared.
            conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        yield engine
        return

    path = tmp_path / "test.db"
    shutil.copy(request.getfixturevalue("migrated_template"), path)
    sqlite_engine = build_engine(f"sqlite:///{path}")
    yield sqlite_engine
    sqlite_engine.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    return build_session_factory(engine)
