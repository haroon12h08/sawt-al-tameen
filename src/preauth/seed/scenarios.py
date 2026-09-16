"""Demonstration scenarios, driven entirely through the application services.

Because the scenarios use the same services as the API, every demo case has a genuine audit trail, evaluation
records, and recommendation history. Nothing is inserted directly into case tables.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from preauth.application.commands import (
    CaseInformationUpdate,
    CreateCaseCommand,
    HumanDecisionCommand,
    RegisterDocumentCommand,
)
from preauth.application.services import ApplicationServices
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, DocumentType, HumanDecisionType, ReviewerRole

VOICE_AGENT = Actor(ActorType.VOICE_AGENT, "voice-agent-dev")
CLINICAL_REVIEWER = Actor(
    ActorType.HUMAN_REVIEWER, "reviewer-clin-001", frozenset({ReviewerRole.CLINICAL_REVIEWER})
)
MEDICAL_DIRECTOR = Actor(
    ActorType.HUMAN_REVIEWER, "reviewer-md-001", frozenset({ReviewerRole.CLINICAL_REVIEWER, ReviewerRole.MEDICAL_DIRECTOR})
)


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    case_id: str
    case_reference: str


def _document(doc_type: DocumentType, n: int) -> RegisterDocumentCommand:
    return RegisterDocumentCommand(
        document_type=doc_type,
        title=f"Synthetic {doc_type.value.replace('_', ' ').lower()}",
        storage_uri=f"docstore://synthetic/{doc_type.value.lower()}-{n}.pdf",
        media_type="application/pdf",
    )


def _intake(services: ApplicationServices, info: dict[str, Any], documents: list[DocumentType]) -> str:
    case = services.cases.create_case(VOICE_AGENT, CreateCaseCommand(information=CaseInformationUpdate(**info)))
    for n, doc_type in enumerate(documents):
        services.cases.register_document(case.id, VOICE_AGENT, _document(doc_type, n))
    return case.id


def _decide(services: ApplicationServices, case_id: str, reviewer: Actor, decision: HumanDecisionType, why: str):
    services.review.assign_reviewer(case_id, reviewer)
    recommendation = services.queries.get_latest_recommendation(case_id, reviewer)
    return services.review.record_decision(
        case_id,
        reviewer,
        HumanDecisionCommand(recommendation_id=recommendation.id, decision=decision, rationale=why),
    )


def run_scenarios(services: ApplicationServices, today: date) -> list[ScenarioResult]:
    service_date = today + timedelta(days=14)
    results: list[tuple[str, str]] = []

    # 1. Complete request -> RECOMMEND_APPROVAL -> reviewer confirms.
    case_id = _intake(
        services,
        dict(provider_number="PRV-100234", patient={"member_id": "MBR-5001-01", "date_of_birth": "1984-03-12"},
             policy_number="POL-000101", procedure_code="PROC-MRI-KNEE", requested_service_date=service_date,
             place_of_service="OUTPATIENT", diagnosis_code="M23.221", diagnosis_description="Old tear, medial meniscus, right knee",
             urgency="STANDARD", conservative_treatment_weeks=8),
        [DocumentType.CLINICAL_NOTES],
    )
    services.evaluation.submit_for_evaluation(case_id, VOICE_AGENT)
    services.review.request_human_review(case_id, VOICE_AGENT)
    _decide(services, case_id, CLINICAL_REVIEWER, HumanDecisionType.APPROVE,
            "Criteria met; documentation supports imaging.")
    results.append(("complete_request_approved", case_id))

    # 2. Missing documentation -> REQUEST_MORE_INFORMATION -> PENDING_INFORMATION.
    case_id = _intake(
        services,
        dict(provider_number="PRV-100871", patient={"member_id": "MBR-5002-01", "date_of_birth": "1971-11-02"},
             policy_number="POL-000102", procedure_code="PROC-KNEE-ARTHROSCOPY", requested_service_date=service_date,
             place_of_service="OUTPATIENT", diagnosis_code="M23.222", urgency="STANDARD",
             conservative_treatment_weeks=10),
        [DocumentType.CLINICAL_NOTES],
    )
    services.evaluation.submit_for_evaluation(case_id, VOICE_AGENT)
    results.append(("missing_documentation", case_id))

    # 3. Rule failure (procedure excluded) -> RECOMMEND_DENIAL -> awaiting clinical review.
    case_id = _intake(
        services,
        dict(provider_number="PRV-100871", patient={"member_id": "MBR-5003-01", "date_of_birth": "1990-07-25"},
             policy_number="POL-000103", procedure_code="PROC-RHINOPLASTY-COSMETIC",
             requested_service_date=service_date, place_of_service="OUTPATIENT", diagnosis_code="J34.2",
             urgency="STANDARD"),
        [DocumentType.CLINICAL_NOTES],
    )
    services.evaluation.submit_for_evaluation(case_id, VOICE_AGENT)
    services.review.request_human_review(case_id, VOICE_AGENT)
    results.append(("rule_failure_denial_recommended", case_id))

    # 4. No coverage terms for the procedure -> ESCALATE -> medical director queue.
    case_id = _intake(
        services,
        dict(provider_number="PRV-100990", patient={"member_id": "MBR-5004-01", "date_of_birth": "1966-01-30"},
             policy_number="POL-000104", procedure_code="PROC-GENETIC-PANEL", requested_service_date=service_date,
             place_of_service="OFFICE", diagnosis_code="Z80.3", urgency="STANDARD"),
        [DocumentType.REFERRAL_LETTER],
    )
    services.evaluation.submit_for_evaluation(case_id, VOICE_AGENT)
    services.review.request_human_review(case_id, VOICE_AGENT)
    results.append(("insufficient_knowledge_escalated", case_id))

    # 5. Conservative-treatment rule fails -> RECOMMEND_DENIAL -> reviewer overrides to APPROVE.
    case_id = _intake(
        services,
        dict(provider_number="PRV-100234", patient={"member_id": "MBR-5005-01", "date_of_birth": "1979-09-09"},
             policy_number="POL-000105", procedure_code="PROC-MRI-KNEE", requested_service_date=service_date,
             place_of_service="OUTPATIENT", diagnosis_code="M25.561", urgency="EXPEDITED",
             conservative_treatment_weeks=3,
             clinical_summary="Acute mechanical locking of the right knee with effusion after twisting injury."),
        [DocumentType.CLINICAL_NOTES],
    )
    services.evaluation.submit_for_evaluation(case_id, VOICE_AGENT)
    services.review.request_human_review(case_id, VOICE_AGENT)
    _decide(services, case_id, CLINICAL_REVIEWER, HumanDecisionType.APPROVE,
            "Mechanical locking is a red-flag presentation; conservative-treatment prerequisite waived.")
    results.append(("human_override_approved", case_id))

    return [
        ScenarioResult(name, cid, services.queries.get_case(cid, CLINICAL_REVIEWER).case_reference)
        for name, cid in results
    ]
