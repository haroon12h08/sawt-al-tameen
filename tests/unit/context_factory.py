from datetime import date

from preauth.domain.enums import DecisionClass, DirectoryStatus, PolicyStatus, Urgency
from preauth.rules.model import (
    CoverageFacts,
    EscalationRuleFacts,
    MemberFacts,
    ProcedureFacts,
    ProviderFacts,
    RequestFacts,
    RuleContext,
    TierFacts,
)

TODAY = date(2026, 9, 17)
TREATMENT = date(2026, 10, 8)

ESCALATION_RULES = {
    rule_id: EscalationRuleFacts(
        rule_id=rule_id, title=f"Rule {rule_id}", situation=f"Situation for {rule_id}", agent_action="Escalate."
    )
    for rule_id in ("ESC-001", "ESC-002", "ESC-003", "ESC-004", "ESC-005", "ESC-006", "ESC-007", "ESC-008")
}

TIERS = {
    "BASIC": (1, 150_000, 1_000, False),
    "ENHANCED": (2, 500_000, 2_500, False),
    "COMPREHENSIVE": (3, 1_000_000, 5_000, False),
    "EXECUTIVE": (4, 3_000_000, 10_000, True),
}

_UNSET = object()


def make_context(
    *,
    tier_id: str = "COMPREHENSIVE",
    policy_status: PolicyStatus = PolicyStatus.ACTIVE,
    policy_start: date = date(2025, 1, 1),
    policy_renewal: date | None = date(2026, 12, 31),
    directory_status: DirectoryStatus = DirectoryStatus.ACTIVE,
    provider_network_rank: int = 1,
    provider_specialties: tuple[str, ...] = ("Orthopaedics", "General Surgery"),
    procedure: object = _UNSET,
    coverage: object = _UNSET,
    covered: bool = True,
    pre_auth_required: bool = True,
    decision_class: DecisionClass = DecisionClass.CLEAR,
    escalation_rule_id: str | None = None,
    category: str = "surgical",
    waiting_months: int = 0,
    required_documents: tuple[str, ...] = ("CLINICAL_NOTES",),
    documents: tuple[str, ...] = ("CLINICAL_NOTES",),
    cost: int = 21_000,
    sub_limit: int | None = None,
    approved_this_year: int = 0,
    urgency: Urgency = Urgency.STANDARD,
    treatment_date: date = TREATMENT,
) -> RuleContext:
    rank, annual_limit, threshold, oon = TIERS[tier_id]
    if procedure is _UNSET:
        procedure = ProcedureFacts(
            procedure_code="SP-20040",
            name="Knee arthroscopy with partial meniscectomy",
            category=category,
            specialty_required="Orthopaedics",
            typical_billed_amount_aed=21_000,
            minimum_tier="BASIC",
            waiting_period_months=waiting_months,
            waiting_period_waived_for_emergency=category in ("emergency", "maternity"),
            decision_class=decision_class,
            escalation_rule_id=escalation_rule_id,
            escalation_reason="Criteria are not settled by the schedule" if escalation_rule_id else None,
            exclusions=(),
            required_documents=required_documents,
        )
    if coverage is _UNSET:
        coverage = CoverageFacts(
            covered=covered,
            pre_authorisation_required=pre_auth_required,
            member_co_payment_percent=10,
            applicable_sub_limit_aed=sub_limit,
            reason_not_covered=None if covered else "Benefit starts at the Executive tier",
            source_document=f"Sawt Assurance {tier_id.title()} Schedule of Benefits 2026",
            source_section="Section 4.16 SP-20040: Knee arthroscopy with partial meniscectomy",
        )
    return RuleContext(
        case_id="case-1",
        as_of=TODAY,
        request=RequestFacts(
            procedure_code="SP-20040",
            treatment_date=treatment_date,
            estimated_cost_aed=cost,
            urgency=urgency,
            registered_document_types=documents,
        ),
        member=MemberFacts(
            member_id="MBR-2026-0011",
            policy_number="POL-SA-2026-100011",
            tier_id=tier_id,
            policy_status=policy_status,
            policy_start_date=policy_start,
            policy_renewal_date=policy_renewal,
            policy_lapse_date=None if policy_status is PolicyStatus.ACTIVE else date(2026, 3, 31),
        ),
        tier=TierFacts(
            tier_id=tier_id,
            name=tier_id.title(),
            tier_rank=rank,
            annual_limit_aed=annual_limit,
            pre_authorisation_threshold_aed=threshold,
            sub_limits_aed={"inpatient": annual_limit, "dental": 6000},
            co_payments_percent={"inpatient": 0, "diagnostics": 10, "outpatient_consultation": 10, "emergency": 0},
            waiting_periods_months={"maternity": 12, "dental": 3, "chronic": 3},
            network_id=f"{tier_id}_NETWORK",
            network_name=f"{tier_id.title()} Network",
            out_of_network_covered=oon,
            source_document=f"Sawt Assurance {tier_id.title()} Schedule of Benefits 2026",
        ),
        provider=ProviderFacts(
            provider_number="PRV-30011",
            name="Al Hudaiba Crescent Hospital",
            emirate="Dubai",
            directory_status=directory_status,
            minimum_network_rank=provider_network_rank,
            specialties=provider_specialties,
        ),
        procedure=procedure,
        coverage=coverage,
        approved_amount_this_year_aed=approved_this_year,
        escalation_rules=ESCALATION_RULES,
    )
