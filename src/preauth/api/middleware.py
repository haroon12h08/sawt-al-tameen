import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from preauth.api.errors import error_envelope
from preauth.infrastructure.observability import actor_var, case_id_var, request_id_var

logger = logging.getLogger("preauth.api.access")

_CASE_ID_IN_PATH = re.compile(r"/cases/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, binds the case id from the path, and writes one access log line per request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request_id_var.set(request_id)
        match = _CASE_ID_IN_PATH.search(request.url.path)
        case_id_var.set(match.group(1) if match else None)
        actor_var.set(None)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Not swallowed: logged with traceback and surfaced as a machine-readable 500.
            logger.exception("unhandled_exception", extra={"method": request.method, "path": request.url.path})
            response = error_envelope(500, "INTERNAL_ERROR", "An unexpected error occurred", {})
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return response
