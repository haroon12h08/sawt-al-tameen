"""Knowledge retrieval: assembles the facts a ruleset needs from case and reference data.

This is the only place that knows how persisted data maps onto ``RuleContext``. A future integration (policy
administration system, provider directory, document classification) would plug in here, not in the rules.
"""

from collections.abc import Callable
from dataclasses import dataclass

from preauth.domain.enums import MissingInformationSource
from preauth.domain.errors import IntegrityViolationError
from preauth.infrastructure.db.models import PreAuthorizationCase
from preauth.rules.model import (
    CoverageFacts,
    DocumentFacts,
    MissingInformation,
    PolicyFacts,
    ProviderFacts,
    RequestFacts,
    RuleContext,
)


@dataclass(frozen=True)
class IntakeRequirement:
    code: str
    description: str
    is_provided: Callable[[PreAuthorizationCase], bool]


INTAKE_REQUIREMENTS: tuple[IntakeRequirement, ...] = (
    IntakeRequirement("intake.provider_number", "Requesting provider number", lambda c: c.provider_id is not None),
    IntakeRequirement(
        "intake.patient", "Member ID and date of birth, verified against membership records",
        lambda c: c.patient_id is not None,
    ),
    IntakeRequirement("intake.policy_number", "Member's policy number", lambda c: c.policy_id is not None),
    IntakeRequirement(
        "intake.procedure_code", "Requested procedure code", lambda c: c.requested_service.procedure_code is not None
    ),
    IntakeRequirement(
        "intake.requested_service_date", "Planned date of service",
        lambda c: c.requested_service.requested_service_date is not None,
    ),
    IntakeRequirement(
        "intake.place_of_service", "Place of service", lambda c: c.requested_service.place_of_service is not None
    ),
    IntakeRequirement("intake.diagnosis_code", "Primary diagnosis (ICD-10)", lambda c: c.diagnosis_code is not None),
    IntakeRequirement("intake.urgency", "Urgency (standard or expedited)", lambda c: c.urgency is not None),
)


def missing_intake(case: PreAuthorizationCase) -> list[MissingInformation]:
    return [
        MissingInformation(code=r.code, description=r.description, source=MissingInformationSource.PROVIDER)
        for r in INTAKE_REQUIREMENTS
        if not r.is_provided(case)
    ]


def build_rule_context(uow, case: PreAuthorizationCase) -> RuleContext:
    """Requires complete intake; callers must validate first."""
    if missing_intake(case):
        raise IntegrityViolationError("Rule context requested for a case with incomplete intake")
    rs = case.requested_service
    policy = case.policy
    coverage = uow.reference.coverage_term(policy.plan_code, rs.procedure_code)
    return RuleContext(
        case_id=case.id,
        as_of=uow.clock.today(),
        request=RequestFacts(
            procedure_code=rs.procedure_code,
            requested_service_date=rs.requested_service_date,
            place_of_service=rs.place_of_service,
            diagnosis_code=case.diagnosis_code,
            urgency=case.urgency,
            conservative_treatment_weeks=case.conservative_treatment_weeks,
        ),
        provider=ProviderFacts(
            provider_number=case.provider.provider_number,
            credentialing_status=case.provider.credentialing_status,
            network_status=case.provider.network_status,
        ),
        policy=PolicyFacts(
            policy_number=policy.policy_number,
            status=policy.status,
            effective_from=policy.effective_from,
            effective_to=policy.effective_to,
            plan_code=policy.plan_code,
            out_of_network_covered=policy.plan.out_of_network_covered,
        ),
        coverage=None
        if coverage is None
        else CoverageFacts(
            plan_code=coverage.plan_code,
            procedure_code=coverage.procedure_code,
            covered=coverage.covered,
            preauth_required=coverage.preauth_required,
            required_document_types=tuple(d.document_type for d in coverage.required_documents),
            indicated_diagnosis_codes=tuple(d.diagnosis_code for d in coverage.indicated_diagnoses),
            min_conservative_treatment_weeks=coverage.min_conservative_treatment_weeks,
            annual_case_limit=coverage.annual_case_limit,
        ),
        documents=tuple(DocumentFacts(document_id=d.id, document_type=d.document_type) for d in case.documents),
        prior_approved_case_count=uow.cases.count_prior_approvals(
            patient_id=case.patient_id,
            procedure_code=rs.procedure_code,
            year=rs.requested_service_date.year,
            exclude_case_id=case.id,
        ),
    )
