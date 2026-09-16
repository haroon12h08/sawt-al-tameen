import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from preauth.infrastructure.db.session import build_engine, build_session_factory

ROOT = Path(__file__).resolve().parents[1]


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


@pytest.fixture
def engine(migrated_template, tmp_path) -> Engine:
    path = tmp_path / "test.db"
    shutil.copy(migrated_template, path)
    eng = build_engine(f"sqlite:///{path}")
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    return build_session_factory(engine)
