"""Process entry point: ``uvicorn preauth.main:app``."""

from preauth.api.app import create_app
from preauth.application.services import build_services
from preauth.infrastructure.db.session import build_engine, build_session_factory
from preauth.infrastructure.observability import configure_logging
from preauth.infrastructure.settings import Settings

settings = Settings.from_env()
configure_logging(settings.log_level)
app = create_app(build_services(build_session_factory(build_engine(settings.database_url))))
