"""Structured JSON logging with request- and case-scoped context.

``request_id`` and ``case_id`` are held in context variables so that every log line emitted while handling a
request carries them without having to be passed explicitly.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
case_id_var: ContextVar[str | None] = ContextVar("case_id", default=None)
actor_var: ContextVar[str | None] = ContextVar("actor", default=None)
# Voice-platform conversation handling the current request, when the request arrives through the voice channel.
conversation_id_var: ContextVar[str | None] = ContextVar("conversation_id", default=None)

_CONTEXT_FIELDS = ("request_id", "case_id", "actor", "conversation_id")
_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime", *_CONTEXT_FIELDS}
_base_record_factory = logging.getLogRecordFactory()


def bind_case_id(case_id: str) -> None:
    case_id_var.set(case_id)


def _record_factory(*args, **kwargs) -> logging.LogRecord:
    # Capture context when the record is created; handlers may format it later, outside the request context.
    record = _base_record_factory(*args, **kwargs)
    record.request_id = request_id_var.get()
    record.case_id = case_id_var.get()
    record.actor = actor_var.get()
    record.conversation_id = conversation_id_var.get()
    return record


def install_log_context() -> None:
    """Idempotently attach request/case/actor context to every log record at creation time."""
    if logging.getLogRecordFactory() is not _record_factory:
        logging.setLogRecordFactory(_record_factory)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
            "case_id": getattr(record, "case_id", None),
            "actor": getattr(record, "actor", None),
            "conversation_id": getattr(record, "conversation_id", None),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    install_log_context()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
