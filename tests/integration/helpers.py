from datetime import date, timedelta

from preauth.application.commands import (
    CaseInformationUpdate,
    CreateCaseCommand,
    HumanDecisionCommand,
    RegisterDocumentCommand,
)
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, DocumentType, HumanDecisionType, ReviewerRole

AGENT = Actor(ActorType.VOICE_AGENT, "voice-agent-test")
PORTAL = Actor(ActorType.PROVIDER_PORTAL, "portal-user-1")
SYSTEM = Actor(ActorType.SYSTEM, "preauth-system")
REVIEWER = Actor(ActorType.HUMAN_REVIEWER, "rev-1", frozenset({ReviewerRole.CLINICAL_REVIEWER}))
OTHER_REVIEWER = Actor(ActorType.HUMAN_REVIEWER, "rev-2", frozenset({ReviewerRole.CLINICAL_REVIEWER}))
DIRECTOR = Actor(
    ActorType.HUMAN_REVIEWER, "md-1", frozenset({ReviewerRole.CLINICAL_REVIEWER, ReviewerRole.MEDICAL_DIRECTOR})
)

SERVICE_DATE = date(2026, 9, 16) + timedelta(days=14)


def complete_info(**overrides) -> CaseInformationUpdate:
    info = dict(
        provider_number="PRV-100234",
        patient={"member_id": "MBR-5001-01", "date_of_birth": "1984-03-12"},
        policy_number="POL-000101",
        procedure_code="PROC-MRI-KNEE",
        requested_service_date=SERVICE_DATE,
        place_of_service="OUTPATIENT",
        diagnosis_code="M23.221",
        urgency="STANDARD",
        conservative_treatment_weeks=8,
    )
    info.update(overrides)
    return CaseInformationUpdate(**{k: v for k, v in info.items() if v is not None})


def document(doc_type: DocumentType = DocumentType.CLINICAL_NOTES) -> RegisterDocumentCommand:
    return RegisterDocumentCommand(
        document_type=doc_type,
        title="Synthetic document",
        storage_uri=f"docstore://test/{doc_type.value.lower()}.pdf",
        media_type="application/pdf",
    )


def new_case(services, info: CaseInformationUpdate | None = None, docs=(DocumentType.CLINICAL_NOTES,)) -> str:
    case = services.cases.create_case(AGENT, CreateCaseCommand(information=info or complete_info()))
    for d in docs:
        services.cases.register_document(case.id, AGENT, document(d))
    return case.id


def evaluated_case(services, info=None, docs=(DocumentType.CLINICAL_NOTES,)):
    case_id = new_case(services, info, docs)
    return case_id, services.evaluation.submit_for_evaluation(case_id, AGENT)


def case_in_review(services, info=None, docs=(DocumentType.CLINICAL_NOTES,)):
    case_id, result = evaluated_case(services, info, docs)
    services.review.request_human_review(case_id, AGENT)
    return case_id, result.recommendation


def decide(services, case_id, reviewer, decision: HumanDecisionType, recommendation_id=None, assign=True):
    if assign:
        services.review.assign_reviewer(case_id, reviewer)
    rec_id = recommendation_id or services.queries.get_latest_recommendation(case_id, reviewer).id
    return services.review.record_decision(
        case_id,
        reviewer,
        HumanDecisionCommand(recommendation_id=rec_id, decision=decision, rationale="Reviewed against criteria."),
    )


def event_types(services, case_id) -> list[str]:
    return [e.event_type.value for e in services.queries.get_audit_history(case_id, REVIEWER)]
