"""Demonstration cases, driven entirely through the application services.

Because the scenarios use the same services as the voice tools, every demo case has a genuine audit trail,
evaluation records and recommendation history. Nothing is inserted directly into case tables.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from preauth.application.commands import (
    CoverageCheckCommand,
    HumanDecisionCommand,
    LogTranscriptCommand,
    RegisterDocumentCommand,
    VerifyCallerCommand,
)
from preauth.application.services import ApplicationServices
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, CallOutcome, DocumentType, HumanDecisionType, ReviewerRole

VOICE_AGENT = Actor(ActorType.VOICE_AGENT, "voice-agent-dev")
PROVIDER_PORTAL = Actor(ActorType.PROVIDER_PORTAL, "portal-dev")
CLINICAL_REVIEWER = Actor(
    ActorType.HUMAN_REVIEWER, "reviewer-clin-001", frozenset({ReviewerRole.CLINICAL_REVIEWER})
)
MEDICAL_DIRECTOR = Actor(
    ActorType.HUMAN_REVIEWER,
    "reviewer-md-001",
    frozenset({ReviewerRole.CLINICAL_REVIEWER, ReviewerRole.MEDICAL_DIRECTOR}),
)


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    case_reference: str
    outcome: str
    status: str


def _verify(services, *, provider: str, organisation: str, policy: str, dob: str, caller: str):
    return services.desk.verify_caller(
        VOICE_AGENT,
        VerifyCallerCommand(
            caller_role="PROVIDER_STAFF",
            organisation_name=organisation,
            caller_reference=provider,
            caller_name=caller,
            member_policy_number=policy,
            member_date_of_birth=date.fromisoformat(dob),
        ),
    )


def _check(services, verification, **kwargs):
    return services.desk.check_coverage_rule(
        VOICE_AGENT, CoverageCheckCommand(verification_id=verification.verification_id, **kwargs)
    )


def _documents(services, case_id: str, types: list[DocumentType]) -> None:
    for document_type in types:
        services.cases.register_document(
            case_id,
            PROVIDER_PORTAL,
            RegisterDocumentCommand(
                document_type=document_type,
                title=f"Synthetic {document_type.value.replace('_', ' ').lower()}",
                storage_uri=f"docstore://synthetic/{document_type.value.lower()}.pdf",
                media_type="application/pdf",
            ),
        )


def _log(services, verification, result, outcome: CallOutcome, summary: str) -> None:
    services.desk.log_transcript(
        VOICE_AGENT,
        LogTranscriptCommand(
            summary=summary,
            outcome_communicated=outcome,
            verification_id=verification.verification_id,
            case_reference=result.case_reference,
        ),
    )


def run_scenarios(services: ApplicationServices, today: date | None = None) -> list[ScenarioResult]:
    today = today or date.today()
    treatment = (today + timedelta(days=21)).isoformat()
    results: list[ScenarioResult] = []

    def record(name: str, result) -> None:
        status = services.queries.get_status(result.case_id, CLINICAL_REVIEWER)
        results.append(
            ScenarioResult(name, result.case_reference, result.outcome.value, status.status.value)
        )

    # 1. Complete request with documents -> recommendation to approve -> reviewer confirms.
    verification = _verify(
        services, provider="PRV-30011", organisation="Al Hudaiba Crescent Hospital",
        policy="POL-SA-2026-100001", dob="1986-04-17", caller="Aisha Rahman",
    )
    first = _check(services, verification, procedure_code="SP-10040", treatment_date=treatment, estimated_cost_aed=2600)
    _documents(services, first.case_id, [DocumentType.CLINICAL_NOTES])
    approved = _check(
        services, verification, procedure_code="SP-10040", treatment_date=treatment,
        estimated_cost_aed=2600, case_reference=first.case_reference,
    )
    _log(services, verification, approved, CallOutcome.RECOMMENDATION_PREPARED,
         "MRI brain pre-authorisation requested; recommendation prepared for sign-off.")
    services.review.assign_reviewer(approved.case_id, CLINICAL_REVIEWER)
    recommendation = services.queries.get_latest_recommendation(approved.case_id, CLINICAL_REVIEWER)
    services.review.record_decision(
        approved.case_id,
        CLINICAL_REVIEWER,
        HumanDecisionCommand(
            recommendation_id=recommendation.id,
            decision=HumanDecisionType.APPROVE,
            rationale="Criteria met and clinical notes support the request.",
        ),
    )
    record("complete_request_approved", approved)

    # 2. Missing documentation -> more information requested.
    verification = _verify(
        services, provider="PRV-30021", organisation="Corniche Lagoon Hospital",
        policy="POL-SA-2026-100004", dob="1975-01-22", caller="Mahmoud Selim",
    )
    more_info = _check(
        services, verification, procedure_code="SP-20020", treatment_date=treatment, estimated_cost_aed=24000
    )
    _log(services, verification, more_info, CallOutcome.MORE_INFORMATION_REQUESTED,
         "Laparoscopic cholecystectomy requested; operative plan and clinical notes outstanding.")
    record("missing_documentation", more_info)

    # 3. Benefit starts at a higher tier -> recommendation to decline.
    verification = _verify(
        services, provider="PRV-30011", organisation="Al Hudaiba Crescent Hospital",
        policy="POL-SA-2026-100003", dob="1991-07-29", caller="Grace Lim",
    )
    denial = _check(
        services, verification, procedure_code="SP-20050", treatment_date=treatment, estimated_cost_aed=62000
    )
    _log(services, verification, denial, CallOutcome.RECOMMENDATION_PREPARED,
         "Total knee replacement requested on the Basic tier; recommendation prepared for sign-off.")
    record("tier_boundary_denial_recommended", denial)

    # 4. Ambiguous procedure -> escalation citing its rule.
    verification = _verify(
        services, provider="PRV-30023", organisation="Yas Horizon Specialist Hospital",
        policy="POL-SA-2026-100011", dob="1981-05-02", caller="Noura Al Ameri",
    )
    escalated = _check(
        services, verification, procedure_code="SP-20110", treatment_date=treatment, estimated_cost_aed=48000
    )
    _log(services, verification, escalated, CallOutcome.ESCALATED,
         "Sleeve gastrectomy requested; referred to the medical director for eligibility criteria.")
    record("ambiguous_escalated", escalated)

    # 5. Reviewer overrides a recommendation to decline.
    verification = _verify(
        services, provider="PRV-30012", organisation="Jumeirah Dunes Specialist Centre",
        policy="POL-SA-2026-100002", dob="1979-11-03", caller="Rami Haddad",
    )
    override = _check(
        services, verification, procedure_code="SP-20140", treatment_date=treatment, estimated_cost_aed=32000
    )
    _log(services, verification, override, CallOutcome.RECOMMENDATION_PREPARED,
         "Rhinoplasty requested; recommendation prepared for sign-off.")
    services.review.assign_reviewer(override.case_id, CLINICAL_REVIEWER)
    recommendation = services.queries.get_latest_recommendation(override.case_id, CLINICAL_REVIEWER)
    services.review.record_decision(
        override.case_id,
        CLINICAL_REVIEWER,
        HumanDecisionCommand(
            recommendation_id=recommendation.id,
            decision=HumanDecisionType.APPROVE,
            rationale="Documented post-traumatic deformity; reconstructive rather than cosmetic on review.",
        ),
    )
    record("human_override_approved", override)

    return results
