"""Actor extraction.

AUTHENTICATION ASSUMPTION: this service sits behind an API gateway / identity-aware proxy that authenticates the
caller (provider portal session, voice-agent service credential, or insurer staff SSO) and forwards the verified
identity in these headers, stripping any client-supplied copies. The service must never be exposed directly.
"""

import hmac
from typing import Annotated

from fastapi import Depends, Header, Request

from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, ReviewerRole
from preauth.domain.errors import DomainError
from preauth.infrastructure.observability import actor_var


class ActorRequiredError(DomainError):
    code = "ACTOR_REQUIRED"


async def current_actor(
    request: Request,
    x_gateway_secret: Annotated[
        str | None, Header(description="Shared gateway secret, required when the deployment configures one")
    ] = None,
    x_actor_type: Annotated[str | None, Header(description="Verified actor type set by the gateway")] = None,
    x_actor_id: Annotated[str | None, Header(description="Verified actor identifier set by the gateway")] = None,
    x_actor_roles: Annotated[
        str | None, Header(description="Comma-separated reviewer roles (HUMAN_REVIEWER only)")
    ] = None,
) -> Actor:
    expected_secret = request.app.state.settings.gateway_secret
    if expected_secret is not None and not hmac.compare_digest(
        (x_gateway_secret or "").encode(), expected_secret.encode()
    ):
        raise ActorRequiredError("Missing or invalid X-Gateway-Secret", code="GATEWAY_SECRET_INVALID")
    if not x_actor_type or not x_actor_id:
        raise ActorRequiredError("X-Actor-Type and X-Actor-Id headers are required")
    try:
        actor_type = ActorType(x_actor_type)
    except ValueError:
        raise ActorRequiredError(
            "Unknown actor type", code="INVALID_ACTOR", details={"allowed": [t.value for t in ActorType]}
        ) from None
    if not 1 <= len(x_actor_id) <= 100:
        raise ActorRequiredError("Actor id must be 1-100 characters", code="INVALID_ACTOR")
    roles: frozenset[ReviewerRole] = frozenset()
    if x_actor_roles:
        if actor_type is not ActorType.HUMAN_REVIEWER:
            raise ActorRequiredError("Only HUMAN_REVIEWER actors may carry roles", code="INVALID_ACTOR")
        try:
            roles = frozenset(ReviewerRole(r.strip()) for r in x_actor_roles.split(",") if r.strip())
        except ValueError:
            raise ActorRequiredError(
                "Unknown reviewer role", code="INVALID_ACTOR", details={"allowed": [r.value for r in ReviewerRole]}
            ) from None
    actor = Actor(type=actor_type, id=x_actor_id, roles=roles)
    actor_var.set(f"{actor.type.value}:{actor.id}")
    return actor


ActorDep = Annotated[Actor, Depends(current_actor)]
