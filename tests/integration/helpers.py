from datetime import date, timedelta

from preauth.application.commands import (
    CoverageCheckCommand,
    HumanDecisionCommand,
    LogTranscriptCommand,
    RegisterDocumentCommand,
    VerifyCallerCommand,
)
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, DocumentType, ReviewerRole

AGENT = Actor(ActorType.VOICE_AGENT, "voice-agent-test")
PORTAL = Actor(ActorType.PROVIDER_PORTAL, "portal-user-1")
SYSTEM = Actor(ActorType.SYSTEM, "preauth-system")
REVIEWER = Actor(ActorType.HUMAN_REVIEWER, "rev-1", frozenset({ReviewerRole.CLINICAL_REVIEWER}))
OTHER_REVIEWER = Actor(ActorType.HUMAN_REVIEWER, "rev-2", frozenset({ReviewerRole.CLINICAL_REVIEWER}))
DIRECTOR = Actor(
    ActorType.HUMAN_REVIEWER, "md-1", frozenset({ReviewerRole.CLINICAL_REVIEWER, ReviewerRole.MEDICAL_DIRECTOR})
)

TREATMENT_DATE = (date.today() + timedelta(days=21)).isoformat()

# Catalogue fixtures used across the integration tests.
ORTHO_HOSPITAL = "PRV-30011"          # Al Hudaiba Crescent Hospital, Basic network, Orthopaedics
BARIATRIC_HOSPITAL = "PRV-30023"      # Yas Horizon, Comprehensive network, Bariatric Surgery
EXECUTIVE_ONLY_HOSPITAL = "PRV-30030"  # Gulf Meridian, Executive network only
SUSPENDED_CLINIC = "PRV-30020"        # Mirdif Vision, suspended
PLASTICS_CLINIC = "PRV-30012"         # Jumeirah Dunes, Enhanced network, Plastic Surgery

EXECUTIVE_MEMBER = ("POL-SA-2026-100001", date(1986, 4, 17))     # Fatima Al Mansoori, long tenure
ENHANCED_MEMBER = ("POL-SA-2026-100002", date(1979, 11, 3))      # Rajesh Nair, long tenure
BASIC_MEMBER = ("POL-SA-2026-100003", date(1991, 7, 29))         # Maria Villanueva, standard tenure
COMPREHENSIVE_MEMBER = ("POL-SA-2026-100011", date(1981, 5, 2))  # Omar Al Balushi, long tenure
NEW_MEMBER = ("POL-SA-2026-100007", date(1993, 2, 19))           # Layla Haddad, joined three months ago
LAPSED_MEMBER = ("POL-SA-2026-100008", date(1990, 12, 4))        # Mohammed Rahman, lapsed


def verify(
    services,
    *,
    provider=ORTHO_HOSPITAL,
    member=EXECUTIVE_MEMBER,
    caller_role="PROVIDER_STAFF",
    organisation="Al Hudaiba Crescent Hospital",
    caller_name="Aisha Rahman",
    actor=AGENT,
):
    policy, dob = member if member else (None, None)
    return services.desk.verify_caller(
        actor,
        VerifyCallerCommand(
            caller_role=caller_role,
            organisation_name=organisation,
            caller_reference=provider,
            caller_name=caller_name,
            member_policy_number=policy,
            member_date_of_birth=dob,
        ),
    )


def check(services, verification, *, procedure_code="SP-20040", cost=21000, actor=AGENT, **kwargs):
    return services.desk.check_coverage_rule(
        actor,
        CoverageCheckCommand(
            verification_id=verification.verification_id,
            procedure_code=procedure_code,
            treatment_date=kwargs.pop("treatment_date", TREATMENT_DATE),
            estimated_cost_aed=cost,
            **kwargs,
        ),
    )


def document(doc_type: DocumentType = DocumentType.CLINICAL_NOTES) -> RegisterDocumentCommand:
    return RegisterDocumentCommand(
        document_type=doc_type,
        title="Synthetic document",
        storage_uri=f"docstore://test/{doc_type.value.lower()}.pdf",
        media_type="application/pdf",
    )


def add_documents(services, case_id, types, actor=PORTAL):
    for document_type in types:
        services.cases.register_document(case_id, actor, document(document_type))


def log(services, *, outcome, case_reference=None, verification=None, summary="Call handled by the desk.", **kwargs):
    return services.desk.log_transcript(
        AGENT,
        LogTranscriptCommand(
            summary=summary,
            outcome_communicated=outcome,
            case_reference=case_reference,
            verification_id=verification.verification_id if verification else None,
            **kwargs,
        ),
    )


def approved_case(services, **kwargs):
    """A case whose documents are complete, so the rules recommend approval."""
    verification = verify(services, **kwargs)
    first = check(services, verification, procedure_code="SP-20040", cost=21000)
    add_documents(services, first.case_id, [DocumentType.CLINICAL_NOTES, DocumentType.OPERATIVE_PLAN,
                                            DocumentType.PRIOR_TREATMENT_RECORD])
    return verification, check(
        services, verification, procedure_code="SP-20040", cost=21000, case_reference=first.case_reference
    )


def decide(services, case_id, reviewer, decision, recommendation_id=None, assign=True):
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
