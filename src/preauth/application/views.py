"""Output contracts for application use cases, and mappers from persistence models.

Views are built inside the unit of work so that nothing outside the application layer touches ORM objects.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from preauth.domain.case_state import allowed_targets
from preauth.domain.enums import (
    ActorType,
    AuditEventType,
    CallbackReason,
    CallbackStatus,
    CallerRole,
    CaseStatus,
    CloseReason,
    CredentialingStatus,
    DocumentType,
    HumanDecisionType,
    NetworkStatus,
    PlaceOfService,
    PolicyStatus,
    RecommendationOutcome,
    ReviewerRole,
    ReviewQueue,
    RuleOutcome,
    Urgency,
)
from preauth.domain.review import QUEUE_FOR_STATUS
from preauth.infrastructure.db import models as m
from preauth.rules.model import MissingInformation, SourceReference


class View(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)


class ProviderView(View):
    provider_number: str
    name: str
    network_status: NetworkStatus
    credentialing_status: CredentialingStatus


class PatientView(View):
    member_id: str
    given_name: str
    family_name: str
    date_of_birth: date


class PolicyView(View):
    policy_number: str
    plan_code: str
    status: PolicyStatus
    effective_from: date
    effective_to: date | None


class RequestedServiceView(View):
    procedure_code: str | None
    procedure_description: str | None
    requested_service_date: date | None
    place_of_service: PlaceOfService | None


class DocumentView(View):
    id: str
    document_type: DocumentType
    title: str
    storage_uri: str
    media_type: str
    content_sha256: str | None
    registered_at: datetime
    registered_by_actor_type: ActorType
    registered_by_actor_id: str


class CaseView(View):
    id: str
    case_reference: str
    status: CaseStatus
    urgency: Urgency | None
    provider: ProviderView | None
    patient: PatientView | None
    policy: PolicyView | None
    requested_service: RequestedServiceView
    diagnosis_code: str | None
    diagnosis_description: str | None
    conservative_treatment_weeks: int | None
    clinical_summary: str | None
    caller_name: str | None
    caller_role: CallerRole | None
    documents: list[DocumentView]
    review_queue: ReviewQueue | None
    assigned_reviewer_id: str | None
    review_requested_at: datetime | None
    close_reason: CloseReason | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    version: int


class RuleResultView(View):
    rule_id: str
    rule_version: str
    outcome: RuleOutcome
    explanation: str
    evidence: dict[str, Any]
    missing_information: list[MissingInformation]
    sources: list[SourceReference]


class SourceAttributionView(View):
    document: str
    section: str
    rule_ids: list[str]


class RecommendationView(View):
    id: str
    case_id: str
    evaluation_id: str
    outcome: RecommendationOutcome
    rationale: str
    determining_rule_ids: list[str]
    evidence: dict[str, Any]
    missing_information: list[MissingInformation]
    sources: list[SourceAttributionView]
    engine_name: str
    engine_version: str
    ruleset_name: str
    ruleset_version: str
    rule_results: list[RuleResultView]
    generated_at: datetime
    advisory_only: Literal[True] = True


class ReviewDecisionView(View):
    id: str
    sequence: int
    recommendation_id: str
    decision: HumanDecisionType
    rationale: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    is_override: bool
    from_status: CaseStatus
    to_status: CaseStatus
    decided_at: datetime


class CaseStatusView(View):
    case_id: str
    case_reference: str
    status: CaseStatus
    review_queue: ReviewQueue | None
    possible_next_statuses: list[CaseStatus]
    final_decision: ReviewDecisionView | None
    updated_at: datetime


class IntakeRequirementView(View):
    code: str
    description: str
    provided: bool


class RequiredInformationView(View):
    case_id: str
    status: CaseStatus
    intake_complete: bool
    intake_requirements: list[IntakeRequirementView]
    missing_information: list[MissingInformation]
    ruleset_name: str | None
    ruleset_version: str | None


class EvaluationResultView(View):
    case_id: str
    status: CaseStatus
    validation_passed: bool
    missing_information: list[MissingInformation]
    recommendation: RecommendationView | None


class AuditEventView(View):
    id: str
    sequence: int
    event_type: AuditEventType
    actor_type: ActorType
    actor_id: str | None
    occurred_at: datetime
    request_id: str | None
    data: dict[str, Any]


class ReviewQueueItemView(View):
    case_id: str
    case_reference: str
    status: CaseStatus
    review_queue: ReviewQueue
    urgency: Urgency | None
    procedure_code: str | None
    review_requested_at: datetime | None
    assigned_reviewer_id: str | None


class CallbackView(View):
    id: str
    reference: str
    case_id: str | None
    conversation_id: str | None
    caller_name: str
    caller_organisation: str | None
    caller_role: CallerRole
    callback_phone: str
    preferred_language: str
    reason: CallbackReason
    summary: str
    status: CallbackStatus
    created_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str | None


class CallRecordView(View):
    id: str
    conversation_id: str
    agent_id: str
    platform: str
    status: str | None
    call_duration_secs: int | None
    transcript_summary: str | None
    call_successful: str | None
    transcript: list[dict[str, Any]]
    analysis: dict[str, Any]
    received_at: datetime


class ReviewPacketView(View):
    case: CaseView
    current_recommendation: RecommendationView | None
    recommendation_history: list[RecommendationView]
    decisions: list[ReviewDecisionView]
    calls: list[CallRecordView]
    # Voice conversations that touched this case but whose transcript has not arrived yet. Sign-off is blocked
    # until this list is empty.
    pending_call_conversation_ids: list[str]
    callbacks: list[CallbackView]
    audit_history: list[AuditEventView]


# --------------------------------------------------------------------------- mappers


def case_view(case: m.PreAuthorizationCase) -> CaseView:
    rs = case.requested_service
    return CaseView(
        id=case.id,
        case_reference=case.case_reference,
        status=case.status,
        urgency=case.urgency,
        provider=ProviderView.model_validate(case.provider) if case.provider else None,
        patient=PatientView.model_validate(case.patient) if case.patient else None,
        policy=PolicyView.model_validate(case.policy) if case.policy else None,
        requested_service=RequestedServiceView(
            procedure_code=rs.procedure_code,
            procedure_description=rs.procedure.description if rs.procedure else None,
            requested_service_date=rs.requested_service_date,
            place_of_service=rs.place_of_service,
        ),
        diagnosis_code=case.diagnosis_code,
        diagnosis_description=case.diagnosis_description,
        conservative_treatment_weeks=case.conservative_treatment_weeks,
        clinical_summary=case.clinical_summary,
        caller_name=case.caller_name,
        caller_role=case.caller_role,
        documents=[DocumentView.model_validate(d) for d in case.documents],
        review_queue=QUEUE_FOR_STATUS.get(case.status),
        assigned_reviewer_id=case.assigned_reviewer_id,
        review_requested_at=case.review_requested_at,
        close_reason=case.close_reason,
        created_at=case.created_at,
        updated_at=case.updated_at,
        closed_at=case.closed_at,
        version=case.version,
    )


def recommendation_view(rec: m.Recommendation) -> RecommendationView:
    evaluation = rec.evaluation
    return RecommendationView(
        id=rec.id,
        case_id=rec.case_id,
        evaluation_id=rec.evaluation_id,
        outcome=rec.outcome,
        rationale=rec.rationale,
        determining_rule_ids=rec.determining_rule_ids,
        evidence=rec.evidence,
        missing_information=rec.missing_information,
        sources=rec.sources,
        engine_name=rec.engine_name,
        engine_version=rec.engine_version,
        ruleset_name=evaluation.engine_name,
        ruleset_version=evaluation.engine_version,
        rule_results=[RuleResultView.model_validate(r) for r in evaluation.results],
        generated_at=rec.generated_at,
    )


def status_view(case: m.PreAuthorizationCase, final_decision: m.ReviewDecision | None) -> CaseStatusView:
    return CaseStatusView(
        case_id=case.id,
        case_reference=case.case_reference,
        status=case.status,
        review_queue=QUEUE_FOR_STATUS.get(case.status),
        possible_next_statuses=sorted(allowed_targets(case.status)),
        final_decision=ReviewDecisionView.model_validate(final_decision) if final_decision else None,
        updated_at=case.updated_at,
    )
