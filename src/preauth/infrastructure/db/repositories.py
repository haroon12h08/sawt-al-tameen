from collections.abc import Sequence
from datetime import date

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from preauth.domain.enums import CallbackStatus, CaseStatus, HumanDecisionType
from preauth.domain.errors import IntegrityViolationError, NotFoundError
from preauth.infrastructure.db.models import (
    AuditEvent,
    CallbackRequest,
    CallRecord,
    CaseDocument,
    CoverageTerm,
    Patient,
    Policy,
    PreAuthorizationCase,
    Procedure,
    Provider,
    Recommendation,
    RequestedService,
    ReviewDecision,
    RuleDefinition,
    RuleEvaluation,
    VoiceToolInvocation,
)


class ReferenceDataRepository:
    def __init__(self, session: Session):
        self._s = session

    def provider_by_number(self, provider_number: str) -> Provider | None:
        return self._s.scalar(select(Provider).where(Provider.provider_number == provider_number))

    def patient_by_member_id(self, member_id: str) -> Patient | None:
        return self._s.scalar(select(Patient).where(Patient.member_id == member_id))

    def policy_by_number(self, policy_number: str) -> Policy | None:
        return self._s.scalar(
            select(Policy).options(selectinload(Policy.plan)).where(Policy.policy_number == policy_number)
        )

    def procedure(self, procedure_code: str) -> Procedure | None:
        return self._s.get(Procedure, procedure_code)

    def coverage_term(self, plan_code: str, procedure_code: str) -> CoverageTerm | None:
        return self._s.scalar(
            select(CoverageTerm)
            .options(selectinload(CoverageTerm.required_documents), selectinload(CoverageTerm.indicated_diagnoses))
            .where(CoverageTerm.plan_code == plan_code, CoverageTerm.procedure_code == procedure_code)
        )


class CaseRepository:
    def __init__(self, session: Session):
        self._s = session

    def add(self, case: PreAuthorizationCase) -> None:
        self._s.add(case)

    def add_document(self, document: CaseDocument) -> None:
        self._s.add(document)

    def get(self, case_id: str) -> PreAuthorizationCase:
        case = self._s.scalar(
            select(PreAuthorizationCase)
            .options(
                selectinload(PreAuthorizationCase.provider),
                selectinload(PreAuthorizationCase.patient),
                selectinload(PreAuthorizationCase.policy).selectinload(Policy.plan),
                selectinload(PreAuthorizationCase.requested_service).selectinload(RequestedService.procedure),
                selectinload(PreAuthorizationCase.documents),
            )
            .where(PreAuthorizationCase.id == case_id)
        )
        if case is None:
            raise NotFoundError(f"Case {case_id} not found", code="CASE_NOT_FOUND", details={"case_id": case_id})
        return case

    def id_for_reference(self, case_reference: str) -> str:
        case_id = self._s.scalar(
            select(PreAuthorizationCase.id).where(PreAuthorizationCase.case_reference == case_reference)
        )
        if case_id is None:
            raise NotFoundError(
                f"Case reference {case_reference} not found",
                code="CASE_NOT_FOUND",
                details={"case_reference": case_reference},
            )
        return case_id

    def count_prior_approvals(
        self, *, patient_id: str, procedure_code: str, year: int, exclude_case_id: str
    ) -> int:
        """Cases for the same member and procedure in the same service year that a human approved."""
        approved = exists().where(
            ReviewDecision.case_id == PreAuthorizationCase.id,
            ReviewDecision.decision == HumanDecisionType.APPROVE,
        )
        return self._s.scalar(
            select(func.count(PreAuthorizationCase.id))
            .join(RequestedService, RequestedService.case_id == PreAuthorizationCase.id)
            .where(
                PreAuthorizationCase.patient_id == patient_id,
                PreAuthorizationCase.id != exclude_case_id,
                RequestedService.procedure_code == procedure_code,
                RequestedService.requested_service_date >= date(year, 1, 1),
                RequestedService.requested_service_date <= date(year, 12, 31),
                approved,
            )
        ) or 0

    def in_statuses(self, statuses: Sequence[CaseStatus], limit: int) -> list[PreAuthorizationCase]:
        return list(
            self._s.scalars(
                select(PreAuthorizationCase)
                .options(selectinload(PreAuthorizationCase.requested_service))
                .where(PreAuthorizationCase.status.in_(statuses))
                .order_by(PreAuthorizationCase.review_requested_at, PreAuthorizationCase.id)
                .limit(limit)
            )
        )


class EvaluationRepository:
    def __init__(self, session: Session):
        self._s = session

    def ensure_rule_definition(self, definition: RuleDefinition) -> None:
        """Register a rule version. A registered (rule_id, version) must never change meaning."""
        existing = self._s.get(RuleDefinition, (definition.rule_id, definition.version))
        if existing is None:
            self._s.add(definition)
            self._s.flush()
        elif existing.description != definition.description or existing.category != definition.category:
            raise IntegrityViolationError(
                "Rule definition changed without a version bump",
                details={"rule_id": definition.rule_id, "version": definition.version},
            )

    def next_evaluation_sequence(self, case_id: str) -> int:
        current = self._s.scalar(select(func.max(RuleEvaluation.sequence)).where(RuleEvaluation.case_id == case_id))
        return (current or 0) + 1

    def add_evaluation(self, evaluation: RuleEvaluation) -> None:
        self._s.add(evaluation)

    def add_recommendation(self, recommendation: Recommendation) -> None:
        self._s.add(recommendation)

    def latest_recommendation(self, case_id: str) -> Recommendation | None:
        return self._s.scalar(
            select(Recommendation)
            .join(RuleEvaluation, Recommendation.evaluation_id == RuleEvaluation.id)
            .options(selectinload(Recommendation.evaluation).selectinload(RuleEvaluation.results))
            .where(Recommendation.case_id == case_id)
            .order_by(RuleEvaluation.sequence.desc())
            .limit(1)
        )

    def recommendations(self, case_id: str) -> list[Recommendation]:
        return list(
            self._s.scalars(
                select(Recommendation)
                .join(RuleEvaluation, Recommendation.evaluation_id == RuleEvaluation.id)
                .options(selectinload(Recommendation.evaluation).selectinload(RuleEvaluation.results))
                .where(Recommendation.case_id == case_id)
                .order_by(RuleEvaluation.sequence)
            )
        )


class ReviewRepository:
    def __init__(self, session: Session):
        self._s = session

    def next_decision_sequence(self, case_id: str) -> int:
        current = self._s.scalar(select(func.max(ReviewDecision.sequence)).where(ReviewDecision.case_id == case_id))
        return (current or 0) + 1

    def add_decision(self, decision: ReviewDecision) -> None:
        self._s.add(decision)

    def decisions(self, case_id: str) -> list[ReviewDecision]:
        return list(
            self._s.scalars(
                select(ReviewDecision).where(ReviewDecision.case_id == case_id).order_by(ReviewDecision.sequence)
            )
        )


class AuditRepository:
    def __init__(self, session: Session):
        self._s = session

    def max_sequence(self, case_id: str) -> int:
        return self._s.scalar(select(func.max(AuditEvent.sequence)).where(AuditEvent.case_id == case_id)) or 0

    def add(self, event: AuditEvent) -> None:
        self._s.add(event)

    def for_case(self, case_id: str) -> list[AuditEvent]:
        return list(
            self._s.scalars(select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.sequence))
        )


class VoiceChannelRepository:
    def __init__(self, session: Session):
        self._s = session

    def add_invocation(self, invocation: VoiceToolInvocation) -> None:
        self._s.add(invocation)

    def conversation_ids_for_case(self, case_id: str) -> list[str]:
        return list(
            self._s.scalars(
                select(VoiceToolInvocation.conversation_id)
                .where(VoiceToolInvocation.case_id == case_id)
                .group_by(VoiceToolInvocation.conversation_id)
                .order_by(func.min(VoiceToolInvocation.invoked_at))
            )
        )

    def case_ids_for_conversation(self, conversation_id: str) -> list[str]:
        return list(
            self._s.scalars(
                select(VoiceToolInvocation.case_id)
                .where(VoiceToolInvocation.conversation_id == conversation_id, VoiceToolInvocation.case_id.is_not(None))
                .group_by(VoiceToolInvocation.case_id)
                .order_by(func.min(VoiceToolInvocation.invoked_at))
            )
        )

    def call_record(self, conversation_id: str) -> CallRecord | None:
        return self._s.scalar(select(CallRecord).where(CallRecord.conversation_id == conversation_id))

    def call_records(self, conversation_ids: Sequence[str]) -> list[CallRecord]:
        if not conversation_ids:
            return []
        return list(
            self._s.scalars(
                select(CallRecord).where(CallRecord.conversation_id.in_(conversation_ids)).order_by(CallRecord.received_at)
            )
        )

    def add_call_record(self, record: CallRecord) -> None:
        self._s.add(record)

    def add_callback(self, callback: CallbackRequest) -> None:
        self._s.add(callback)

    def callback(self, callback_id: str) -> CallbackRequest:
        callback = self._s.get(CallbackRequest, callback_id)
        if callback is None:
            raise NotFoundError(
                f"Callback request {callback_id} not found",
                code="CALLBACK_NOT_FOUND",
                details={"callback_id": callback_id},
            )
        return callback

    def callbacks(self, status: CallbackStatus | None, limit: int) -> list[CallbackRequest]:
        query = select(CallbackRequest).order_by(CallbackRequest.created_at).limit(limit)
        if status is not None:
            query = query.where(CallbackRequest.status == status)
        return list(self._s.scalars(query))

    def callbacks_for_case(self, case_id: str) -> list[CallbackRequest]:
        return list(
            self._s.scalars(
                select(CallbackRequest).where(CallbackRequest.case_id == case_id).order_by(CallbackRequest.created_at)
            )
        )
