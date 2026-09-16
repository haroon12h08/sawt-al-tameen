from fastapi import FastAPI

from preauth.agent_tools.toolbox import AgentToolbox
from preauth.api.errors import install_error_handlers
from preauth.api.middleware import RequestContextMiddleware
from preauth.api.routes import agent, cases, review
from preauth.application.services import ApplicationServices
from preauth.infrastructure.observability import install_log_context

DESCRIPTION = """
Backend for provider pre-authorisation requests: case intake, validation, rule evaluation, advisory
recommendations, human review, and an immutable audit trail.

**The system never issues final authorisations or denials.** Only an authorised human reviewer can move a case
to `APPROVED` or `DENIED`.

**Authentication assumption:** requests arrive through a gateway that authenticates the caller and sets
`X-Actor-Type`, `X-Actor-Id`, and (for reviewers) `X-Actor-Roles`. The service must not be exposed directly.

Every response carries `X-Request-ID`. Errors use a uniform envelope: `{"error": {"code", "message", "details",
"request_id", "case_id"}}`.
"""


def create_app(services: ApplicationServices) -> FastAPI:
    install_log_context()
    app = FastAPI(title="Pre-Authorisation Case Service", version="0.1.0", description=DESCRIPTION)
    app.state.services = services
    app.state.toolbox = AgentToolbox(services)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(cases.router)
    app.include_router(review.router)
    app.include_router(agent.router)

    @app.get("/health", tags=["Operations"], summary="Liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
