"""Human hand-off for requests the agent must not handle: ambiguous, non-rule-based, or out of scope.

A callback request is how the escalation node of a voice workflow ends: the caller is told a qualified person
will call back, and staff see the request in a queue.
"""

import logging

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.commands import RequestCallbackCommand, ResolveCallbackCommand
from preauth.application.review_service import require_human_reviewer
from preauth.application.unit_of_work import UnitOfWork
from preauth.application.views import CallbackView
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, AuditEventType, CallbackStatus
from preauth.domain.errors import AuthorizationError, OperationNotAllowedError
from preauth.infrastructure.clock import Clock, new_case_reference, new_id
from preauth.infrastructure.db.models import CallbackRequest
from preauth.infrastructure.observability import bind_case_id, conversation_id_var

logger = logging.getLogger("preauth.callbacks")

_REQUESTERS = frozenset({ActorType.VOICE_AGENT, ActorType.PROVIDER_PORTAL})


class CallbackService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock):
        self._session_factory = session_factory
        self._clock = clock

    def _uow(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory, self._clock)

    def request_callback(self, actor: Actor, command: RequestCallbackCommand) -> CallbackView:
        if actor.type not in _REQUESTERS:
            raise AuthorizationError(
                "Only provider-facing channels may request a callback",
                code="INTAKE_ACTOR_REQUIRED",
                details={"actor_type": actor.type},
            )
        with self._uow() as uow:
            if command.case_id is not None:
                bind_case_id(command.case_id)
                uow.cases.get(command.case_id)
            callback = CallbackRequest(
                id=new_id(),
                reference=new_case_reference().replace("PA-", "CB-"),
                case_id=command.case_id,
                conversation_id=conversation_id_var.get(),
                caller_name=command.caller_name,
                caller_organisation=command.caller_organisation,
                caller_role=command.caller_role,
                callback_phone=command.callback_phone,
                preferred_language=command.preferred_language,
                reason=command.reason,
                summary=command.summary,
                status=CallbackStatus.OPEN,
                created_at=self._clock.now(),
                created_by_actor_type=actor.type,
                created_by_actor_id=actor.id,
            )
            uow.voice.add_callback(callback)
            uow.flush()
            if command.case_id is not None:
                uow.audit.record(
                    command.case_id,
                    AuditEventType.HUMAN_CALLBACK_REQUESTED,
                    actor,
                    {
                        "callback_id": callback.id,
                        "callback_reference": callback.reference,
                        "reason": command.reason,
                        "summary": command.summary,
                        "preferred_language": command.preferred_language,
                    },
                )
            uow.commit()
            logger.info("callback_requested", extra={"reason": command.reason.value})
            return CallbackView.model_validate(callback)

    def list_callbacks(self, actor: Actor, status: CallbackStatus | None, limit: int = 50) -> list[CallbackView]:
        require_human_reviewer(actor)
        with self._uow() as uow:
            return [CallbackView.model_validate(c) for c in uow.voice.callbacks(status, limit)]

    def resolve_callback(self, callback_id: str, actor: Actor, command: ResolveCallbackCommand) -> CallbackView:
        require_human_reviewer(actor)
        with self._uow() as uow:
            callback = uow.voice.callback(callback_id)
            if callback.status is not CallbackStatus.OPEN:
                raise OperationNotAllowedError(
                    "Callback request is already resolved",
                    code="CALLBACK_ALREADY_RESOLVED",
                    details={"callback_id": callback_id},
                )
            callback.status = CallbackStatus.RESOLVED
            callback.resolved_at = self._clock.now()
            callback.resolved_by = actor.id
            callback.resolution_note = command.resolution_note
            if callback.case_id is not None:
                bind_case_id(callback.case_id)
                uow.audit.record(
                    callback.case_id,
                    AuditEventType.HUMAN_CALLBACK_RESOLVED,
                    actor,
                    {"callback_id": callback.id, "resolution_note": command.resolution_note},
                )
            uow.commit()
            return CallbackView.model_validate(callback)
