"""Knowledge retrieval: assembles the facts the ruleset needs from the catalogue and the case.

This is the only place that knows how the catalogue tables map onto ``RuleContext``. Replacing the synthetic
catalogue with a real policy administration system means changing this module and the loader, not the rules.
"""

from collections.abc import Callable
from dataclasses import dataclass

from preauth.domain.enums import MissingInformationSource
from preauth.domain.errors import IntegrityViolationError
from preauth.infrastructure.db.models import PreAuthorizationCase
from preauth.rules.model import (
    CoverageFacts,
    EscalationRuleFacts,
    MemberFacts,
    MissingInformation,
    ProcedureFacts,
    ProviderFacts,
    RequestFacts,
    RuleContext,
    TierFacts,
)


@dataclass(frozen=True)
class IntakeRequirement:
    code: str
    description: str
    is_provided: Callable[[PreAuthorizationCase], bool]


INTAKE_REQUIREMENTS: tuple[IntakeRequirement, ...] = (
    IntakeRequirement("intake.provider", "Requesting provider number", lambda c: c.provider_id is not None),
    IntakeRequirement("intake.member", "Member policy number, verified", lambda c: c.member_id is not None),
    IntakeRequirement("intake.procedure_code", "Procedure code", lambda c: c.procedure_code is not None),
    IntakeRequirement("intake.treatment_date", "Planned treatment date", lambda c: c.treatment_date is not None),
    IntakeRequirement(
        "intake.estimated_cost_aed", "Estimated cost in AED", lambda c: c.estimated_cost_aed is not None
    ),
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

    member, provider = case.member, case.provider
    tier = member.tier
    # The procedure may be absent from the schedule entirely; that is a finding, not an error.
    procedure = uow.catalogue.procedure(case.procedure_code)
    coverage = uow.catalogue.coverage(tier.tier_id, case.procedure_code) if procedure else None

    return RuleContext(
        case_id=case.id,
        as_of=uow.clock.today(),
        request=RequestFacts(
            procedure_code=case.procedure_code,
            treatment_date=case.treatment_date,
            estimated_cost_aed=case.estimated_cost_aed,
            urgency=case.urgency,
            registered_document_types=tuple(sorted({d.document_type.value for d in case.documents})),
            diagnosis_code=case.diagnosis_code,
        ),
        member=MemberFacts(
            member_id=member.member_id,
            policy_number=member.policy_number,
            tier_id=member.tier_id,
            policy_status=member.policy_status,
            policy_start_date=member.policy_start_date,
            policy_renewal_date=member.policy_renewal_date,
            policy_lapse_date=member.policy_lapse_date,
        ),
        tier=TierFacts(
            tier_id=tier.tier_id,
            name=tier.name,
            tier_rank=tier.tier_rank,
            annual_limit_aed=tier.annual_limit_aed,
            pre_authorisation_threshold_aed=tier.pre_authorisation_threshold_aed,
            sub_limits_aed=tier.sub_limits_aed,
            co_payments_percent=tier.co_payments_percent,
            waiting_periods_months=tier.waiting_periods_months,
            network_id=tier.network_id,
            network_name=tier.network_name,
            out_of_network_covered=tier.out_of_network_covered,
            source_document=tier.source_document,
        ),
        provider=ProviderFacts(
            provider_number=provider.provider_number,
            name=provider.name,
            emirate=provider.emirate,
            directory_status=provider.directory_status,
            minimum_network_rank=provider.minimum_network_rank,
            specialties=tuple(provider.specialties),
        ),
        procedure=None
        if procedure is None
        else ProcedureFacts(
            procedure_code=procedure.procedure_code,
            name=procedure.name,
            category=procedure.category,
            specialty_required=procedure.specialty_required,
            typical_billed_amount_aed=procedure.typical_billed_amount_aed,
            minimum_tier=procedure.minimum_tier,
            waiting_period_months=procedure.waiting_period_months,
            waiting_period_waived_for_emergency=procedure.waiting_period_waived_for_emergency,
            decision_class=procedure.decision_class,
            escalation_rule_id=procedure.escalation_rule_id,
            escalation_reason=procedure.escalation_reason,
            exclusions=tuple(procedure.exclusions),
            required_documents=tuple(procedure.required_documents),
        ),
        coverage=None
        if coverage is None
        else CoverageFacts(
            covered=coverage.covered,
            pre_authorisation_required=coverage.pre_authorisation_required,
            member_co_payment_percent=coverage.member_co_payment_percent,
            applicable_sub_limit_aed=coverage.applicable_sub_limit_aed,
            reason_not_covered=coverage.reason_not_covered,
            source_document=coverage.source_document,
            source_section=coverage.source_section,
        ),
        approved_amount_this_year_aed=uow.cases.approved_amount_this_year_aed(
            member_id=member.id, year=case.treatment_date.year, exclude_case_id=case.id
        ),
        escalation_rules={
            rule.rule_id: EscalationRuleFacts(
                rule_id=rule.rule_id,
                title=rule.title,
                situation=rule.situation,
                agent_action=rule.agent_action,
            )
            for rule in uow.catalogue.escalation_rules()
        },
    )
