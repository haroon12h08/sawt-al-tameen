from collections.abc import Sequence
from datetime import date

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from preauth.domain.enums import CallbackStatus, CaseStatus, HumanDecisionType
from preauth.domain.errors import IntegrityViolationError, NotFoundError
from preauth.infrastructure.db.models import (
    AuditEvent,
    CallbackRequest,
    CallerVerification,
    CallLog,
    CallRecord,
    CaseDocument,
    CoverageTerm,
    EscalationRule,
    Member,
    OnboardingApplication,
    OnboardingRequirement,
    PolicyTier,
    PreAuthorizationCase,
    Procedure,
    Provider,
    Recommendation,
    ReviewDecision,
    RuleDefinition,
    RuleEvaluation,
    VoiceToolInvocation,
)


class CatalogueRepository:
    """Reads the benefit catalogue loaded from knowledge_base/."""

    def __init__(self, session: Session):
        self._s = session

    def tier(self, tier_id: str) -> PolicyTier | None:
        return self._s.get(PolicyTier, tier_id)

    def provider_by_number(self, provider_number: str) -> Provider | None:
        return self._s.scalar(select(Provider).where(Provider.provider_number == provider_number))

    def member_by_policy_number(self, policy_number: str) -> Member | None:
        return self._s.scalar(
            select(Member).options(selectinload(Member.tier)).where(Member.policy_number == policy_number)
        )

    def member_by_member_id(self, member_id: str) -> Member | None:
        return self._s.scalar(
            select(Member).options(selectinload(Member.tier)).where(Member.member_id == member_id)
        )

    def procedure(self, procedure_code: str) -> Procedure | None:
        return self._s.get(Procedure, procedure_code)

    def coverage(self, tier_id: str, procedure_code: str) -> CoverageTerm | None:
        return self._s.scalar(
            select(CoverageTerm).where(
                CoverageTerm.tier_id == tier_id, CoverageTerm.procedure_code == procedure_code
            )
        )

    def escalation_rules(self) -> list[EscalationRule]:
        return list(self._s.scalars(select(EscalationRule).order_by(EscalationRule.rule_id)))

    def onboarding_requirements(self) -> list[OnboardingRequirement]:
        return list(
            self._s.scalars(select(OnboardingRequirement).order_by(OnboardingRequirement.requirement_id))
        )

    def onboarding_application(self, application_id: str) -> OnboardingApplication | None:
        return self._s.get(OnboardingApplication, application_id)


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
                selectinload(PreAuthorizationCase.member).selectinload(Member.tier),
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

    def approved_amount_this_year_aed(self, *, member_id: str, year: int, exclude_case_id: str) -> int:
        """Amount already approved by a human for this member in the treatment year."""
        approved = exists().where(
            ReviewDecision.case_id == PreAuthorizationCase.id,
            ReviewDecision.decision == HumanDecisionType.APPROVE,
        )
        total = self._s.scalar(
            select(func.coalesce(func.sum(PreAuthorizationCase.estimated_cost_aed), 0)).where(
                PreAuthorizationCase.member_id == member_id,
                PreAuthorizationCase.id != exclude_case_id,
                PreAuthorizationCase.treatment_date >= date(year, 1, 1),
                PreAuthorizationCase.treatment_date <= date(year, 12, 31),
                approved,
            )
        )
        return int(total or 0)

    def in_statuses(self, statuses: Sequence[CaseStatus], limit: int) -> list[PreAuthorizationCase]:
        return list(
            self._s.scalars(
                select(PreAuthorizationCase)
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
        current = self._s.scalar(
            select(func.max(RuleEvaluation.sequence)).where(RuleEvaluation.case_id == case_id)
        )
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
        current = self._s.scalar(
            select(func.max(ReviewDecision.sequence)).where(ReviewDecision.case_id == case_id)
        )
        return (current or 0) + 1

    def add_decision(self, decision: ReviewDecision) -> None:
        self._s.add(decision)

    def decisions(self, case_id: str) -> list[ReviewDecision]:
        return list(
            self._s.scalars(
                select(ReviewDecision)
                .where(ReviewDecision.case_id == case_id)
                .order_by(ReviewDecision.sequence)
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
            self._s.scalars(
                select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.sequence)
            )
        )


class VoiceChannelRepository:
    def __init__(self, session: Session):
        self._s = session

    # --- caller verification

    def add_verification(self, verification: CallerVerification) -> None:
        self._s.add(verification)

    def verification(self, verification_id: str) -> CallerVerification:
        record = self._s.scalar(
            select(CallerVerification)
            .options(
                selectinload(CallerVerification.provider),
                selectinload(CallerVerification.member).selectinload(Member.tier),
            )
            .where(CallerVerification.id == verification_id)
        )
        if record is None:
            raise NotFoundError(
                "Caller verification not found or expired",
                code="VERIFICATION_NOT_FOUND",
                details={"verification_id": verification_id},
            )
        return record

    # --- call logs

    def add_call_log(self, log: CallLog) -> None:
        self._s.add(log)

    def call_logs_for_case(self, case_id: str) -> list[CallLog]:
        return list(
            self._s.scalars(select(CallLog).where(CallLog.case_id == case_id).order_by(CallLog.logged_at))
        )

    # --- conversations and transcripts

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
                .where(
                    VoiceToolInvocation.conversation_id == conversation_id,
                    VoiceToolInvocation.case_id.is_not(None),
                )
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
                select(CallRecord)
                .where(CallRecord.conversation_id.in_(conversation_ids))
                .order_by(CallRecord.received_at)
            )
        )

    def add_call_record(self, record: CallRecord) -> None:
        self._s.add(record)

    # --- callbacks

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
                select(CallbackRequest)
                .where(CallbackRequest.case_id == case_id)
                .order_by(CallbackRequest.created_at)
            )
        )
