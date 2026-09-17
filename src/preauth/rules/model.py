"""Rules-engine data contracts.

``RuleContext`` is an immutable, JSON-serialisable snapshot of every fact a rule may consult, assembled from the
catalogue tables that ``knowledge_base/`` is loaded into. It is persisted with each evaluation, so any rule result
can be reproduced later by re-running the same ruleset version against the stored snapshot.
"""

from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from preauth.domain.enums import (
    DecisionClass,
    DirectoryStatus,
    MissingInformationSource,
    PolicyStatus,
    RuleCategory,
    RuleOutcome,
    Urgency,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceReference(_Frozen):
    """Where a fact used by a rule is written down (policy document section or register entry)."""

    document: str
    section: str


class MissingInformation(_Frozen):
    code: str
    description: str
    source: MissingInformationSource


class EscalationRuleFacts(_Frozen):
    rule_id: str
    title: str
    situation: str
    agent_action: str


class RuleResult(_Frozen):
    rule_id: str
    rule_version: str
    outcome: RuleOutcome
    explanation: str
    evidence: dict[str, Any]
    missing_information: tuple[MissingInformation, ...] = ()
    sources: tuple[SourceReference, ...] = ()
    # Set when the result means "a human must decide this"; cites a rule from escalation_rules.json.
    escalation_rule_id: str | None = None

    @model_validator(mode="after")
    def _check_outcome_invariants(self) -> "RuleResult":
        if self.outcome is RuleOutcome.UNKNOWN and not self.missing_information:
            raise ValueError("UNKNOWN results must state what information is missing")
        if self.outcome is not RuleOutcome.UNKNOWN and self.missing_information:
            raise ValueError(f"{self.outcome} results must not carry missing information")
        if self.escalation_rule_id and self.outcome is not RuleOutcome.UNKNOWN:
            raise ValueError("Only UNKNOWN results may cite an escalation rule")
        return self


class RequestFacts(_Frozen):
    procedure_code: str
    treatment_date: date
    estimated_cost_aed: int
    urgency: Urgency
    registered_document_types: tuple[str, ...]
    diagnosis_code: str | None = None


class MemberFacts(_Frozen):
    member_id: str
    policy_number: str
    tier_id: str
    policy_status: PolicyStatus
    policy_start_date: date
    policy_renewal_date: date | None
    policy_lapse_date: date | None


class TierFacts(_Frozen):
    tier_id: str
    name: str
    tier_rank: int
    annual_limit_aed: int
    pre_authorisation_threshold_aed: int
    sub_limits_aed: dict[str, int]
    co_payments_percent: dict[str, int]
    waiting_periods_months: dict[str, int]
    network_id: str
    network_name: str
    out_of_network_covered: bool
    source_document: str


class ProviderFacts(_Frozen):
    provider_number: str
    name: str
    emirate: str
    directory_status: DirectoryStatus
    minimum_network_rank: int
    specialties: tuple[str, ...]


class ProcedureFacts(_Frozen):
    procedure_code: str
    name: str
    category: str
    specialty_required: str
    typical_billed_amount_aed: int
    minimum_tier: str
    waiting_period_months: int
    waiting_period_waived_for_emergency: bool
    decision_class: DecisionClass
    escalation_rule_id: str | None
    escalation_reason: str | None
    exclusions: tuple[str, ...]
    required_documents: tuple[str, ...]


class CoverageFacts(_Frozen):
    covered: bool
    pre_authorisation_required: bool
    member_co_payment_percent: int | None
    applicable_sub_limit_aed: int | None
    reason_not_covered: str | None
    source_document: str
    source_section: str


class RuleContext(_Frozen):
    case_id: str
    as_of: date
    request: RequestFacts
    member: MemberFacts
    tier: TierFacts
    provider: ProviderFacts
    # None when the procedure code is not in the benefit schedule at all.
    procedure: ProcedureFacts | None
    coverage: CoverageFacts | None
    approved_amount_this_year_aed: int
    escalation_rules: dict[str, EscalationRuleFacts]

    def escalation(self, rule_id: str) -> EscalationRuleFacts | None:
        return self.escalation_rules.get(rule_id)


class Rule(Protocol):
    rule_id: str
    version: str
    description: str
    category: RuleCategory

    def evaluate(self, ctx: RuleContext) -> RuleResult: ...


class EvaluationOutcome(_Frozen):
    engine_name: str
    engine_version: str
    results: tuple[RuleResult, ...]


class RulesEngine(Protocol):
    name: str
    version: str

    @property
    def rules(self) -> tuple[Rule, ...]: ...

    def evaluate(self, ctx: RuleContext) -> EvaluationOutcome: ...
