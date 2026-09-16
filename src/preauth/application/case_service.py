"""Case intake: creation, information collection, document registration, closure."""

import logging
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.commands import (
    CaseInformationUpdate,
    CloseCaseCommand,
    CreateCaseCommand,
    RegisterDocumentCommand,
)
from preauth.application.unit_of_work import UnitOfWork
from preauth.application.views import CaseView, DocumentView, case_view
from preauth.domain.actors import Actor
from preauth.domain.case_state import EDITABLE_STATES
from preauth.domain.enums import ActorType, AuditEventType, CaseStatus, CloseReason
from preauth.domain.errors import AuthorizationError, OperationNotAllowedError, ValidationFailedError
from preauth.infrastructure.clock import Clock, new_case_reference, new_id
from preauth.infrastructure.db.models import CaseDocument, PreAuthorizationCase, RequestedService
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


class CaseService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock):
        self._session_factory = session_factory
        self._clock = clock

    def _uow(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory, self._clock)

    def create_case(self, actor: Actor, command: CreateCaseCommand) -> CaseView:
        require_intake_actor(actor)
        with self._uow() as uow:
            now = self._clock.now()
            case = PreAuthorizationCase(
                id=new_id(),
                case_reference=new_case_reference(),
                status=CaseStatus.RECEIVED,
                created_by_actor_type=actor.type,
                created_by_actor_id=actor.id,
                created_at=now,
                updated_at=now,
                documents=[],
            )
            case.requested_service = RequestedService(id=new_id(), case_id=case.id)
            bind_case_id(case.id)
            uow.cases.add(case)
            uow.flush()
            uow.audit.record(
                case.id, AuditEventType.CASE_CREATED, actor, {"case_reference": case.case_reference}
            )
            if command.information is not None:
                self._apply_information(uow, case, actor, command.information)
            uow.commit()
            logger.info("case_created", extra={"case_reference": case.case_reference})
            return case_view(uow.cases.get(case.id))

    def update_information(self, case_id: str, actor: Actor, update: CaseInformationUpdate) -> CaseView:
        require_intake_actor(actor)
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            self._apply_information(uow, case, actor, update)
            uow.commit()
            return case_view(uow.cases.get(case_id))

    def register_document(self, case_id: str, actor: Actor, command: RegisterDocumentCommand) -> DocumentView:
        require_intake_actor(actor)
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            self._require_editable(case)
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
            self._enter_information_collection(uow, case, actor, reason="document_registered")
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
            return case_view(uow.cases.get(case_id))

    # ------------------------------------------------------------------ internals

    def _require_editable(self, case: PreAuthorizationCase) -> None:
        if case.status not in EDITABLE_STATES:
            raise OperationNotAllowedError(
                f"Case information cannot be changed while the case is {case.status}",
                code="CASE_NOT_EDITABLE",
                details={"status": case.status, "editable_statuses": sorted(EDITABLE_STATES)},
            )

    def _enter_information_collection(self, uow: UnitOfWork, case, actor: Actor, *, reason: str) -> None:
        if case.status in _REOPENING_STATES:
            uow.transition(case, CaseStatus.INFORMATION_COLLECTION, actor, reason=reason)

    def _apply_information(
        self, uow: UnitOfWork, case: PreAuthorizationCase, actor: Actor, update: CaseInformationUpdate
    ) -> None:
        self._require_editable(case)
        resolved = self._resolve_references(uow, case, update)

        collected: dict[str, Any] = {}
        modified: dict[str, dict[str, Any]] = {}

        def apply(target: object, attr: str, field: str, new_value: Any, display: Any = None) -> None:
            old_value = getattr(target, attr)
            if old_value == new_value:
                return
            setattr(target, attr, new_value)
            shown_new = display if display is not None else new_value
            if old_value is None:
                collected[field] = shown_new
            else:
                modified[field] = {"previous": previous_display.get(field, old_value), "new": shown_new}

        previous_display = {
            "provider_number": case.provider.provider_number if case.provider else None,
            "patient": {"member_id": case.patient.member_id} if case.patient else None,
            "policy_number": case.policy.policy_number if case.policy else None,
        }
        fields = update.model_fields_set
        rs = case.requested_service
        if "provider_number" in fields:
            apply(case, "provider_id", "provider_number", resolved["provider"].id, update.provider_number)
        if "patient" in fields:
            apply(case, "patient_id", "patient", resolved["patient"].id, {"member_id": update.patient.member_id})
        if "policy_number" in fields:
            apply(case, "policy_id", "policy_number", resolved["policy"].id, update.policy_number)
        for field in ("procedure_code", "requested_service_date", "place_of_service"):
            if field in fields:
                apply(rs, field, field, getattr(update, field))
        for field in (
            "diagnosis_code", "diagnosis_description", "urgency", "conservative_treatment_weeks", "clinical_summary"
        ):
            if field in fields:
                apply(case, field, field, getattr(update, field))

        if not collected and not modified:
            return
        if collected:
            uow.audit.record(case.id, AuditEventType.INFORMATION_COLLECTED, actor, {"fields": collected})
        if modified:
            uow.audit.record(case.id, AuditEventType.INFORMATION_MODIFIED, actor, {"fields": modified})
        self._enter_information_collection(uow, case, actor, reason="information_submitted")
        case.updated_at = self._clock.now()
        # Reference relationships are reloaded from the ids on the next read.
        uow.flush()
        uow.session.expire(case, ["provider", "patient", "policy"])
        uow.session.expire(rs, ["procedure"])

    def _resolve_references(
        self, uow: UnitOfWork, case: PreAuthorizationCase, update: CaseInformationUpdate
    ) -> dict[str, Any]:
        """Validates identifiers against reference data. Any failure rejects the whole update."""
        fields = update.model_fields_set
        resolved: dict[str, Any] = {}

        if "provider_number" in fields:
            provider = uow.reference.provider_by_number(update.provider_number)
            if provider is None:
                raise ValidationFailedError(
                    "Provider number is not recognised",
                    code="UNKNOWN_PROVIDER",
                    details={"provider_number": update.provider_number},
                )
            resolved["provider"] = provider

        if "patient" in fields:
            patient = uow.reference.patient_by_member_id(update.patient.member_id)
            # Same error whether the member is unknown or the date of birth mismatches (no membership probing).
            if patient is None or patient.date_of_birth != update.patient.date_of_birth:
                raise ValidationFailedError(
                    "Member could not be verified with the supplied member ID and date of birth",
                    code="MEMBER_NOT_VERIFIED",
                )
            resolved["patient"] = patient

        if "policy_number" in fields:
            policy = uow.reference.policy_by_number(update.policy_number)
            if policy is None:
                raise ValidationFailedError(
                    "Policy number is not recognised",
                    code="UNKNOWN_POLICY",
                    details={"policy_number": update.policy_number},
                )
            resolved["policy"] = policy

        patient_id = resolved["patient"].id if "patient" in resolved else case.patient_id
        policy = resolved.get("policy") or case.policy
        if patient_id is not None and policy is not None and policy.patient_id != patient_id:
            raise ValidationFailedError(
                "Policy does not belong to the identified member",
                code="POLICY_MEMBER_MISMATCH",
                details={"policy_number": policy.policy_number},
            )

        if "procedure_code" in fields and uow.reference.procedure(update.procedure_code) is None:
            raise ValidationFailedError(
                "Procedure code is not recognised",
                code="UNKNOWN_PROCEDURE",
                details={"procedure_code": update.procedure_code},
            )

        if "requested_service_date" in fields and update.requested_service_date < self._clock.today():
            raise ValidationFailedError(
                "Pre-authorisation must be requested before the date of service",
                code="SERVICE_DATE_IN_PAST",
                details={"requested_service_date": update.requested_service_date, "today": self._clock.today()},
            )
        return resolved
