"""Case intake support: document registration and closure.

Cases themselves are opened by the pre-authorisation desk (``desk_service``) when a coverage check is requested.
"""

import logging

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.commands import CloseCaseCommand, RegisterDocumentCommand
from preauth.application.unit_of_work import UnitOfWork
from preauth.application.views import CaseView, DocumentView, case_view
from preauth.domain.actors import Actor
from preauth.domain.case_state import EDITABLE_STATES
from preauth.domain.enums import ActorType, AuditEventType, CaseStatus, CloseReason
from preauth.domain.errors import AuthorizationError, OperationNotAllowedError
from preauth.infrastructure.clock import Clock, new_id
from preauth.infrastructure.db.models import CaseDocument, PreAuthorizationCase
from preauth.infrastructure.observability import bind_case_id

logger = logging.getLogger("preauth.cases")

_INTAKE_ACTORS = frozenset({ActorType.PROVIDER_PORTAL, ActorType.VOICE_AGENT})
_REOPENING_STATES = frozenset(
    {CaseStatus.RECEIVED, CaseStatus.PENDING_INFORMATION, CaseStatus.RECOMMENDATION_READY}
)


def require_intake_actor(actor: Actor) -> None:
    if actor.type not in _INTAKE_ACTORS:
        raise AuthorizationError(
            "Only provider-facing channels may submit case information",
            code="INTAKE_ACTOR_REQUIRED",
            details={"actor_type": actor.type},
        )


def require_editable(case: PreAuthorizationCase) -> None:
    if case.status not in EDITABLE_STATES:
        raise OperationNotAllowedError(
            f"Case information cannot be changed while the case is {case.status}",
            code="CASE_NOT_EDITABLE",
            details={"status": case.status, "editable_statuses": sorted(EDITABLE_STATES)},
        )


def enter_information_collection(uow: UnitOfWork, case: PreAuthorizationCase, actor: Actor, *, reason: str) -> None:
    if case.status in _REOPENING_STATES:
        uow.transition(case, CaseStatus.INFORMATION_COLLECTION, actor, reason=reason)


class CaseService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock):
        self._session_factory = session_factory
        self._clock = clock

    def _uow(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory, self._clock)

    def register_document(self, case_id: str, actor: Actor, command: RegisterDocumentCommand) -> DocumentView:
        """Documents arrive through the provider portal or the regulator's claims channel, never by telephone."""
        require_intake_actor(actor)
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            require_editable(case)
            document = CaseDocument(
                id=new_id(),
                case_id=case.id,
                document_type=command.document_type,
                title=command.title,
                storage_uri=command.storage_uri,
                media_type=command.media_type,
                content_sha256=command.content_sha256,
                registered_at=self._clock.now(),
                registered_by_actor_type=actor.type,
                registered_by_actor_id=actor.id,
            )
            uow.cases.add_document(document)
            uow.audit.record(
                case.id,
                AuditEventType.DOCUMENT_REGISTERED,
                actor,
                {"document_id": document.id, **command.model_dump(mode="json")},
            )
            enter_information_collection(uow, case, actor, reason="document_registered")
            case.updated_at = self._clock.now()
            uow.commit()
            return DocumentView.model_validate(document)

    def close_case(self, case_id: str, actor: Actor, command: CloseCaseCommand) -> CaseView:
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            decided = case.status in (CaseStatus.APPROVED, CaseStatus.DENIED)
            if decided != (command.reason is CloseReason.DECISION_COMMUNICATED):
                raise OperationNotAllowedError(
                    f"Close reason {command.reason} is not valid for a case in status {case.status}",
                    code="INVALID_CLOSE_REASON",
                    details={"status": case.status, "reason": command.reason},
                )
            uow.transition(case, CaseStatus.CLOSED, actor, reason=command.reason.value)
            case.close_reason = command.reason
            case.closed_at = self._clock.now()
            uow.audit.record(
                case.id, AuditEventType.CASE_CLOSED, actor, {"reason": command.reason, "note": command.note}
            )
            uow.commit()
            case = uow.cases.get(case_id)
            return case_view(case, uow.catalogue.procedure(case.procedure_code) if case.procedure_code else None)
