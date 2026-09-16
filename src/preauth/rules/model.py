"""Rules-engine data contracts.

``RuleContext`` is an immutable, JSON-serialisable snapshot of every fact a rule may consult. It is persisted
with each evaluation, so any rule result can be reproduced later by re-running the same ruleset version against
the stored snapshot.
"""

from datetime import date
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, model_validator

from preauth.domain.enums import (
    CredentialingStatus,
    DocumentType,
    MissingInformationSource,
    NetworkStatus,
    PlaceOfService,
    PolicyStatus,
    RuleCategory,
    RuleOutcome,
    Urgency,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MissingInformation(_Frozen):
    code: str
    description: str
    source: MissingInformationSource


class RuleResult(_Frozen):
    rule_id: str
    rule_version: str
    outcome: RuleOutcome
    explanation: str
    evidence: dict[str, Any]
    missing_information: tuple[MissingInformation, ...] = ()

    @model_validator(mode="after")
    def _check_outcome_invariants(self) -> "RuleResult":
        if self.outcome is RuleOutcome.UNKNOWN and not self.missing_information:
            raise ValueError("UNKNOWN results must state what information is missing")
        if self.outcome is not RuleOutcome.UNKNOWN and self.missing_information:
            raise ValueError(f"{self.outcome} results must not carry missing information")
        return self


class RequestFacts(_Frozen):
    procedure_code: str
    requested_service_date: date
    place_of_service: PlaceOfService
    diagnosis_code: str
    urgency: Urgency
    conservative_treatment_weeks: int | None


class ProviderFacts(_Frozen):
    provider_number: str
    credentialing_status: CredentialingStatus
    network_status: NetworkStatus


class PolicyFacts(_Frozen):
    policy_number: str
    status: PolicyStatus
    effective_from: date
    effective_to: date | None
    plan_code: str
    out_of_network_covered: bool


class CoverageFacts(_Frozen):
    plan_code: str
    procedure_code: str
    covered: bool
    preauth_required: bool
    required_document_types: tuple[DocumentType, ...]
    indicated_diagnosis_codes: tuple[str, ...]
    min_conservative_treatment_weeks: int | None
    annual_case_limit: int | None


class DocumentFacts(_Frozen):
    document_id: str
    document_type: DocumentType


class RuleContext(_Frozen):
    case_id: str
    as_of: date
    request: RequestFacts
    provider: ProviderFacts
    policy: PolicyFacts
    coverage: CoverageFacts | None
    documents: tuple[DocumentFacts, ...]
    prior_approved_case_count: int


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
