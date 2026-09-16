from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_url: str) -> Engine:
    engine = create_engine(database_url, future=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
