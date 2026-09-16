from alembic import context

from preauth.infrastructure.db.models import Base
from preauth.infrastructure.db.session import build_engine
from preauth.infrastructure.settings import Settings

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # Tests and tooling may inject a URL programmatically; otherwise use the environment.
    return config.attributes.get("database_url") or Settings.from_env().database_url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = build_engine(_database_url())
    with engine.connect() as conn:
        _run(conn)
    engine.dispose()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
