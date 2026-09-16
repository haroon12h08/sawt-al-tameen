"""Deterministic mock ruleset for phase 1.

These rules are illustrative, not real insurance policy. Each rule consults only the ``RuleContext`` and
returns PASS, FAIL, or UNKNOWN. Missing information always yields UNKNOWN, never FAIL.

Changing a rule's behaviour requires bumping its ``version`` and the ruleset version.
"""

from typing import Any

from preauth.domain.enums import (
    CredentialingStatus,
    MissingInformationSource,
    NetworkStatus,
    PolicyStatus,
    RuleCategory,
    RuleOutcome,
)
from preauth.rules.engine import RuleSetEngine
from preauth.rules.model import MissingInformation, RuleContext, RuleResult

RULESET_NAME = "mock-preauth-ruleset"
RULESET_VERSION = "2026.09.1"

COVERAGE_TERMS_MISSING = MissingInformation(
    code="insurer.coverage_terms",
    description="No coverage terms are defined for this procedure under the member's plan",
    source=MissingInformationSource.INSURER,
)


class _BaseRule:
    rule_id: str
    version: str = "1"
    description: str
    category: RuleCategory

    def _result(
        self,
        outcome: RuleOutcome,
        explanation: str,
        evidence: dict[str, Any],
        missing: tuple[MissingInformation, ...] = (),
    ) -> RuleResult:
        return RuleResult(
            rule_id=self.rule_id,
            rule_version=self.version,
            outcome=outcome,
            explanation=explanation,
            evidence=evidence,
            missing_information=missing,
        )

    def _coverage_unknown(self, ctx: RuleContext) -> RuleResult:
        return self._result(
            RuleOutcome.UNKNOWN,
            "Cannot evaluate: coverage terms for this plan and procedure are not available",
            {"plan_code": ctx.policy.plan_code, "procedure_code": ctx.request.procedure_code},
            (COVERAGE_TERMS_MISSING,),
        )


class PolicyActiveRule(_BaseRule):
    rule_id = "ELIG-001-POLICY-ACTIVE"
    description = "The member's policy is active on the requested service date"
    category = RuleCategory.ELIGIBILITY

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        p, service_date = ctx.policy, ctx.request.requested_service_date
        evidence = {
            "policy_number": p.policy_number,
            "policy_status": p.status,
            "effective_from": p.effective_from.isoformat(),
            "effective_to": p.effective_to.isoformat() if p.effective_to else None,
            "requested_service_date": service_date.isoformat(),
        }
        if p.status is not PolicyStatus.ACTIVE:
            return self._result(RuleOutcome.FAIL, f"Policy status is {p.status}", evidence)
        if service_date < p.effective_from or (p.effective_to is not None and service_date > p.effective_to):
            return self._result(
                RuleOutcome.FAIL, "Requested service date is outside the policy effective period", evidence
            )
        return self._result(RuleOutcome.PASS, "Policy is active on the requested service date", evidence)


class ProviderCredentialedRule(_BaseRule):
    rule_id = "ELIG-002-PROVIDER-CREDENTIALED"
    description = "The requesting provider holds active credentialing with the insurer"
    category = RuleCategory.ELIGIBILITY

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        status = ctx.provider.credentialing_status
        evidence = {"provider_number": ctx.provider.provider_number, "credentialing_status": status}
        if status is CredentialingStatus.ACTIVE:
            return self._result(RuleOutcome.PASS, "Provider credentialing is active", evidence)
        return self._result(RuleOutcome.FAIL, f"Provider credentialing status is {status}", evidence)


class ProviderNetworkRule(_BaseRule):
    rule_id = "ELIG-003-PROVIDER-NETWORK"
    description = "The provider is in network, or the plan covers out-of-network providers"
    category = RuleCategory.ELIGIBILITY

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        evidence = {
            "provider_number": ctx.provider.provider_number,
            "network_status": ctx.provider.network_status,
            "plan_code": ctx.policy.plan_code,
            "plan_out_of_network_covered": ctx.policy.out_of_network_covered,
        }
        if ctx.provider.network_status is NetworkStatus.IN_NETWORK:
            return self._result(RuleOutcome.PASS, "Provider is in network", evidence)
        if ctx.policy.out_of_network_covered:
            return self._result(RuleOutcome.PASS, "Provider is out of network; plan covers out-of-network care", evidence)
        return self._result(
            RuleOutcome.FAIL, "Provider is out of network and the plan does not cover out-of-network care", evidence
        )


class ProcedureCoveredRule(_BaseRule):
    rule_id = "COV-001-PROCEDURE-COVERED"
    description = "The requested procedure is a covered benefit under the member's plan"
    category = RuleCategory.COVERAGE

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        c = ctx.coverage
        if c is None:
            return self._coverage_unknown(ctx)
        evidence = {
            "plan_code": c.plan_code,
            "procedure_code": c.procedure_code,
            "covered": c.covered,
            "preauth_required": c.preauth_required,
        }
        if not c.covered:
            return self._result(RuleOutcome.FAIL, "Procedure is excluded from the member's plan", evidence)
        return self._result(RuleOutcome.PASS, "Procedure is a covered benefit", evidence)


class DiagnosisIndicatedRule(_BaseRule):
    rule_id = "MED-001-DIAGNOSIS-INDICATED"
    description = "The diagnosis is an accepted indication for the requested procedure"
    category = RuleCategory.MEDICAL_NECESSITY

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        c = ctx.coverage
        if c is None:
            return self._coverage_unknown(ctx)
        evidence = {
            "diagnosis_code": ctx.request.diagnosis_code,
            "indicated_diagnosis_codes": list(c.indicated_diagnosis_codes),
        }
        if not c.indicated_diagnosis_codes:
            return self._result(RuleOutcome.PASS, "Coverage terms define no diagnosis restriction", evidence)
        if ctx.request.diagnosis_code in c.indicated_diagnosis_codes:
            return self._result(RuleOutcome.PASS, "Diagnosis is an accepted indication", evidence)
        return self._result(
            RuleOutcome.FAIL, "Diagnosis is not an accepted indication for this procedure", evidence
        )


class RequiredDocumentsRule(_BaseRule):
    rule_id = "DOC-001-REQUIRED-DOCUMENTS"
    description = "All supporting documents required by the coverage terms have been registered"
    category = RuleCategory.DOCUMENTATION

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        c = ctx.coverage
        if c is None:
            return self._coverage_unknown(ctx)
        present = {d.document_type for d in ctx.documents}
        missing_types = [t for t in c.required_document_types if t not in present]
        evidence = {
            "required_document_types": list(c.required_document_types),
            "registered_documents": [
                {"document_id": d.document_id, "document_type": d.document_type} for d in ctx.documents
            ],
        }
        if missing_types:
            return self._result(
                RuleOutcome.UNKNOWN,
                "Required supporting documents have not been provided",
                evidence,
                tuple(
                    MissingInformation(
                        code=f"document.{t}",
                        description=f"Supporting document of type {t}",
                        source=MissingInformationSource.PROVIDER,
                    )
                    for t in missing_types
                ),
            )
        return self._result(RuleOutcome.PASS, "All required documents are present", evidence)


class ConservativeTreatmentRule(_BaseRule):
    rule_id = "MED-002-CONSERVATIVE-TREATMENT"
    description = "Minimum duration of conservative treatment has been completed where the coverage terms require it"
    category = RuleCategory.MEDICAL_NECESSITY

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        c = ctx.coverage
        if c is None:
            return self._coverage_unknown(ctx)
        weeks = ctx.request.conservative_treatment_weeks
        evidence = {
            "min_conservative_treatment_weeks": c.min_conservative_treatment_weeks,
            "reported_conservative_treatment_weeks": weeks,
        }
        if c.min_conservative_treatment_weeks is None:
            return self._result(RuleOutcome.PASS, "Coverage terms require no conservative treatment", evidence)
        if weeks is None:
            return self._result(
                RuleOutcome.UNKNOWN,
                "Duration of conservative treatment has not been reported",
                evidence,
                (
                    MissingInformation(
                        code="clinical.conservative_treatment_weeks",
                        description="Number of weeks of conservative treatment completed before this request",
                        source=MissingInformationSource.PROVIDER,
                    ),
                ),
            )
        if weeks < c.min_conservative_treatment_weeks:
            return self._result(
                RuleOutcome.FAIL,
                f"Reported {weeks} weeks of conservative treatment; minimum is {c.min_conservative_treatment_weeks}",
                evidence,
            )
        return self._result(RuleOutcome.PASS, "Conservative treatment requirement is met", evidence)


class AnnualLimitRule(_BaseRule):
    rule_id = "LIM-001-ANNUAL-CASE-LIMIT"
    description = "Approved requests for this procedure in the service year have not reached the plan limit"
    category = RuleCategory.LIMITS

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        c = ctx.coverage
        if c is None:
            return self._coverage_unknown(ctx)
        evidence = {
            "annual_case_limit": c.annual_case_limit,
            "prior_approved_case_count": ctx.prior_approved_case_count,
            "service_year": ctx.request.requested_service_date.year,
        }
        if c.annual_case_limit is None:
            return self._result(RuleOutcome.PASS, "Coverage terms define no annual limit", evidence)
        if ctx.prior_approved_case_count >= c.annual_case_limit:
            return self._result(RuleOutcome.FAIL, "Annual limit for this procedure has been reached", evidence)
        return self._result(RuleOutcome.PASS, "Within annual limit", evidence)


def build_mock_rules_engine() -> RuleSetEngine:
    return RuleSetEngine(
        name=RULESET_NAME,
        version=RULESET_VERSION,
        rules=[
            PolicyActiveRule(),
            ProviderCredentialedRule(),
            ProviderNetworkRule(),
            ProcedureCoveredRule(),
            DiagnosisIndicatedRule(),
            RequiredDocumentsRule(),
            ConservativeTreatmentRule(),
            AnnualLimitRule(),
        ],
    )
