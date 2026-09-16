from datetime import date

from preauth.domain.enums import (
    CredentialingStatus,
    DocumentType,
    NetworkStatus,
    PlaceOfService,
    PolicyStatus,
    Urgency,
)
from preauth.rules.model import (
    CoverageFacts,
    DocumentFacts,
    PolicyFacts,
    ProviderFacts,
    RequestFacts,
    RuleContext,
)

_UNSET = object()


def make_context(
    *,
    policy_status=PolicyStatus.ACTIVE,
    effective_to=None,
    credentialing=CredentialingStatus.ACTIVE,
    network=NetworkStatus.IN_NETWORK,
    oon_covered=False,
    coverage=_UNSET,
    covered=True,
    preauth_required=True,
    diagnosis="M23.221",
    documents=(DocumentType.CLINICAL_NOTES, DocumentType.IMAGING_REPORT),
    conservative_weeks=8,
    min_weeks=6,
    limit=2,
    prior_approved=0,
    service_date=date(2026, 10, 1),
) -> RuleContext:
    if coverage is _UNSET:
        coverage = CoverageFacts(
            plan_code="PLAN-GOLD-PPO",
            procedure_code="PROC-KNEE-ARTHROSCOPY",
            covered=covered,
            preauth_required=preauth_required,
            required_document_types=(DocumentType.CLINICAL_NOTES, DocumentType.IMAGING_REPORT),
            indicated_diagnosis_codes=("M23.221", "M23.222"),
            min_conservative_treatment_weeks=min_weeks,
            annual_case_limit=limit,
        )
    return RuleContext(
        case_id="case-1",
        as_of=date(2026, 9, 16),
        request=RequestFacts(
            procedure_code="PROC-KNEE-ARTHROSCOPY",
            requested_service_date=service_date,
            place_of_service=PlaceOfService.OUTPATIENT,
            diagnosis_code=diagnosis,
            urgency=Urgency.STANDARD,
            conservative_treatment_weeks=conservative_weeks,
        ),
        provider=ProviderFacts(
            provider_number="PRV-100234", credentialing_status=credentialing, network_status=network
        ),
        policy=PolicyFacts(
            policy_number="POL-1",
            status=policy_status,
            effective_from=date(2026, 1, 1),
            effective_to=effective_to,
            plan_code="PLAN-GOLD-PPO",
            out_of_network_covered=oon_covered,
        ),
        coverage=coverage,
        documents=tuple(DocumentFacts(document_id=f"doc-{i}", document_type=t) for i, t in enumerate(documents)),
        prior_approved_case_count=prior_approved,
    )
