"""Pre-authorisation ruleset for the UAE tiered catalogue.

Every rule reads only the ``RuleContext`` assembled from the catalogue in ``knowledge_base/``: tier limits and
thresholds, network nesting, the per-tier coverage row, waiting periods and required documents. A rule that
cannot be decided from the schedule returns UNKNOWN and cites the escalation rule (ESC-###) that applies, so the
reviewer and the caller get the same reason.

These rules are illustrative and operate on synthetic data. They are not real insurance policy.
"""

from typing import Any

from preauth.domain.enums import (
    DecisionClass,
    DirectoryStatus,
    MissingInformationSource,
    PolicyStatus,
    RuleCategory,
    RuleOutcome,
)
from preauth.rules.engine import RuleSetEngine
from preauth.rules.model import MissingInformation, RuleContext, RuleResult, SourceReference

RULESET_NAME = "uae-preauth-ruleset"
RULESET_VERSION = "2026.09.1"

MEMBER_REGISTER = "Membership and policy register"
PROVIDER_REGISTER = "Provider network and credentialing register"
ESCALATION_DOCUMENT = "Escalation rules"


def _months_between(start, end) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month) - (1 if end.day < start.day else 0)


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
        *,
        missing: tuple[MissingInformation, ...] = (),
        sources: tuple[SourceReference, ...] = (),
        escalation_rule_id: str | None = None,
    ) -> RuleResult:
        return RuleResult(
            rule_id=self.rule_id,
            rule_version=self.version,
            outcome=outcome,
            explanation=explanation,
            evidence=evidence,
            missing_information=missing,
            sources=sources,
            escalation_rule_id=escalation_rule_id,
        )

    def _escalate(
        self, ctx: RuleContext, rule_id: str, explanation: str, evidence: dict[str, Any], *,
        sources: tuple[SourceReference, ...] = (), code: str | None = None,
        source: MissingInformationSource = MissingInformationSource.INSURER,
    ) -> RuleResult:
        """UNKNOWN + an escalation citation: the schedule cannot settle this, so a human must."""
        rule = ctx.escalation(rule_id)
        description = f"{rule.title}: {rule.situation}" if rule else f"Escalation rule {rule_id}"
        return self._result(
            RuleOutcome.UNKNOWN,
            explanation,
            {**evidence, "escalation_rule": rule.model_dump() if rule else {"rule_id": rule_id}},
            missing=(
                MissingInformation(code=code or f"escalation.{rule_id}", description=description, source=source),
            ),
            sources=sources + (SourceReference(document=ESCALATION_DOCUMENT, section=rule_id),),
            escalation_rule_id=rule_id,
        )

    def _tier_source(self, ctx: RuleContext, section: str) -> SourceReference:
        return SourceReference(document=ctx.tier.source_document, section=section)

    def _coverage_source(self, ctx: RuleContext) -> tuple[SourceReference, ...]:
        c = ctx.coverage
        return (SourceReference(document=c.source_document, section=c.source_section),) if c else ()


class PolicyActiveRule(_BaseRule):
    rule_id = "ELIG-001-POLICY-ACTIVE"
    description = "The member's policy is active on the treatment date"
    category = RuleCategory.ELIGIBILITY

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        m, treatment = ctx.member, ctx.request.treatment_date
        evidence = {
            "policy_number": m.policy_number,
            "policy_status": m.policy_status,
            "policy_start_date": m.policy_start_date.isoformat(),
            "policy_lapse_date": m.policy_lapse_date.isoformat() if m.policy_lapse_date else None,
            "treatment_date": treatment.isoformat(),
        }
        sources = (SourceReference(document=MEMBER_REGISTER, section=f"Policy {m.policy_number}"),)
        if m.policy_status is not PolicyStatus.ACTIVE:
            return self._result(
                RuleOutcome.FAIL, f"Policy {m.policy_number} is {m.policy_status.value.lower()}", evidence,
                sources=sources,
            )
        if treatment < m.policy_start_date:
            return self._result(
                RuleOutcome.FAIL, "Treatment date falls before the policy start date", evidence, sources=sources
            )
        if m.policy_renewal_date and treatment > m.policy_renewal_date:
            return self._result(
                RuleOutcome.FAIL, "Treatment date falls after the policy renewal date", evidence, sources=sources
            )
        return self._result(RuleOutcome.PASS, "Policy is active on the treatment date", evidence, sources=sources)


class WaitingPeriodRule(_BaseRule):
    rule_id = "ELIG-002-WAITING-PERIOD"
    description = "Any waiting period that applies to the benefit has been served"
    category = RuleCategory.ELIGIBILITY

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        procedure = ctx.procedure
        if procedure is None:
            return self._result(RuleOutcome.PASS, "No benefit identified; waiting period not applicable", {})
        tier_waiting = ctx.tier.waiting_periods_months.get(procedure.category, 0)
        required = max(procedure.waiting_period_months, tier_waiting)
        served = _months_between(ctx.member.policy_start_date, ctx.request.treatment_date)
        evidence = {
            "required_waiting_months": required,
            "months_since_policy_start": served,
            "policy_start_date": ctx.member.policy_start_date.isoformat(),
            "category": procedure.category,
        }
        sources = (self._tier_source(ctx, "Section 1 General"),)
        if required == 0 or served >= required:
            return self._result(
                RuleOutcome.PASS, "No outstanding waiting period for this benefit", evidence, sources=sources
            )
        if procedure.waiting_period_waived_for_emergency and ctx.request.urgency.value == "EXPEDITED":
            return self._result(
                RuleOutcome.PASS, "Waiting period waived for an emergency presentation", evidence, sources=sources
            )
        return self._escalate(
            ctx, "ESC-007",
            f"Treatment falls inside the {required}-month waiting period ({served} months served)",
            evidence, sources=sources, code="eligibility.waiting_period",
        )


class ProviderDirectoryRule(_BaseRule):
    rule_id = "NET-001-PROVIDER-DIRECTORY"
    description = "The requesting provider is active in the provider directory"
    category = RuleCategory.NETWORK

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        p = ctx.provider
        evidence = {"provider_number": p.provider_number, "directory_status": p.directory_status}
        sources = (SourceReference(document=PROVIDER_REGISTER, section=f"Provider {p.provider_number}"),)
        if p.directory_status is DirectoryStatus.ACTIVE:
            return self._result(RuleOutcome.PASS, "Provider is active in the directory", evidence, sources=sources)
        return self._escalate(
            ctx, "ESC-008",
            f"Provider {p.provider_number} is {p.directory_status.value.replace('_', ' ').lower()}",
            evidence, sources=sources, code="network.provider_status",
        )


class NetworkAccessRule(_BaseRule):
    rule_id = "NET-002-NETWORK-ACCESS"
    description = "The provider is inside the member's network, or the tier covers out-of-network care"
    category = RuleCategory.NETWORK

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        evidence = {
            "provider_minimum_network_rank": ctx.provider.minimum_network_rank,
            "member_tier": ctx.tier.tier_id,
            "member_tier_rank": ctx.tier.tier_rank,
            "network": ctx.tier.network_name,
            "out_of_network_covered": ctx.tier.out_of_network_covered,
        }
        sources = (self._tier_source(ctx, "Section 2 Eligibility"),)
        # Networks nest: Basic ⊂ Enhanced ⊂ Comprehensive ⊂ Executive.
        if ctx.provider.minimum_network_rank <= ctx.tier.tier_rank:
            return self._result(
                RuleOutcome.PASS,
                f"Provider is inside the {ctx.tier.network_name}",
                evidence, sources=sources,
            )
        if ctx.tier.out_of_network_covered:
            return self._result(
                RuleOutcome.PASS,
                "Provider is outside the member's network; the tier covers out-of-network treatment",
                evidence, sources=sources,
            )
        return self._result(
            RuleOutcome.FAIL,
            f"Provider is outside the {ctx.tier.network_name} and the tier does not cover out-of-network care",
            evidence, sources=sources,
        )


class SpecialtyRule(_BaseRule):
    rule_id = "NET-003-PROVIDER-SPECIALTY"
    description = "The provider is credentialed for the specialty the procedure requires"
    category = RuleCategory.NETWORK

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        if ctx.procedure is None:
            return self._result(RuleOutcome.PASS, "No benefit identified; specialty not applicable", {})
        evidence = {
            "specialty_required": ctx.procedure.specialty_required,
            "provider_specialties": list(ctx.provider.specialties),
        }
        sources = (SourceReference(document=PROVIDER_REGISTER, section=f"Provider {ctx.provider.provider_number}"),)
        if ctx.procedure.specialty_required in ctx.provider.specialties:
            return self._result(
                RuleOutcome.PASS, "Provider is credentialed for the required specialty", evidence, sources=sources
            )
        # A credentialing gap is a network matter, not a benefit denial: the member may be redirected to a
        # facility that offers the specialty, so this goes to the network team rather than becoming a refusal.
        return self._escalate(
            ctx, "ESC-008",
            f"Provider {ctx.provider.provider_number} is not credentialed for "
            f"{ctx.procedure.specialty_required}",
            evidence, sources=sources, code="network.provider_specialty",
        )


class ProcedureInScheduleRule(_BaseRule):
    rule_id = "COV-001-PROCEDURE-IN-SCHEDULE"
    description = "The procedure appears in the benefit schedule"
    category = RuleCategory.COVERAGE

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        evidence = {"procedure_code": ctx.request.procedure_code, "tier": ctx.tier.tier_id}
        if ctx.procedure is not None and ctx.coverage is not None:
            return self._result(
                RuleOutcome.PASS, "Procedure is listed in the benefit schedule", evidence,
                sources=self._coverage_source(ctx),
            )
        return self._escalate(
            ctx, "ESC-005",
            f"Procedure {ctx.request.procedure_code} has no entry in the benefit schedule",
            evidence, code="insurer.benefit_schedule_entry",
        )


class TierCoverageRule(_BaseRule):
    rule_id = "COV-002-TIER-COVERS-PROCEDURE"
    description = "The member's tier covers the procedure"
    category = RuleCategory.COVERAGE

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        if ctx.coverage is None:
            return self._result(
                RuleOutcome.PASS, "Cover cannot be assessed without a schedule entry; see COV-001",
                {"procedure_code": ctx.request.procedure_code},
            )
        c = ctx.coverage
        evidence = {
            "tier": ctx.tier.tier_id,
            "covered": c.covered,
            "minimum_tier": ctx.procedure.minimum_tier if ctx.procedure else None,
            "member_co_payment_percent": c.member_co_payment_percent,
            "exclusions": list(ctx.procedure.exclusions) if ctx.procedure else [],
        }
        if c.covered:
            return self._result(
                RuleOutcome.PASS,
                f"Covered on the {ctx.tier.name} tier with a {c.member_co_payment_percent}% member co-payment",
                evidence, sources=self._coverage_source(ctx),
            )
        return self._result(
            RuleOutcome.FAIL, c.reason_not_covered or "Not covered on this tier", evidence,
            sources=self._coverage_source(ctx),
        )


class DecisionClassRule(_BaseRule):
    rule_id = "COV-003-SCHEDULE-DECIDABLE"
    description = "The benefit schedule settles this procedure without clinical judgement"
    category = RuleCategory.COVERAGE

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        procedure = ctx.procedure
        if procedure is None:
            return self._result(RuleOutcome.PASS, "No schedule entry; see COV-001", {})
        evidence = {"decision_class": procedure.decision_class, "procedure_code": procedure.procedure_code}
        if procedure.decision_class is not DecisionClass.AMBIGUOUS:
            return self._result(
                RuleOutcome.PASS, "The schedule settles this procedure", evidence,
                sources=self._coverage_source(ctx),
            )
        return self._escalate(
            ctx, procedure.escalation_rule_id or "ESC-005",
            procedure.escalation_reason or "The schedule does not settle this procedure",
            evidence, sources=self._coverage_source(ctx), code="clinical.review_required",
        )


class RequiredDocumentsRule(_BaseRule):
    rule_id = "DOC-001-REQUIRED-DOCUMENTS"
    description = "Supporting documents required by the schedule have been received"
    category = RuleCategory.DOCUMENTATION

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        procedure, coverage = ctx.procedure, ctx.coverage
        if procedure is None or coverage is None or not coverage.pre_authorisation_required:
            return self._result(RuleOutcome.PASS, "No supporting documents required", {})
        present = set(ctx.request.registered_document_types)
        missing_types = [d for d in procedure.required_documents if d not in present]
        evidence = {
            "required_documents": list(procedure.required_documents),
            "received_documents": sorted(present),
        }
        if not missing_types:
            return self._result(
                RuleOutcome.PASS, "All required supporting documents have been received", evidence,
                sources=self._coverage_source(ctx),
            )
        rule = ctx.escalation("ESC-001")
        return self._result(
            RuleOutcome.UNKNOWN,
            "Required supporting documents have not been received",
            {**evidence, "escalation_rule": rule.model_dump() if rule else {"rule_id": "ESC-001"}},
            missing=tuple(
                MissingInformation(
                    code=f"document.{document_type}",
                    description=f"{document_type.replace('_', ' ').capitalize()} for {procedure.name}",
                    source=MissingInformationSource.PROVIDER,
                )
                for document_type in missing_types
            ),
            sources=self._coverage_source(ctx) + (SourceReference(document=ESCALATION_DOCUMENT, section="ESC-001"),),
            escalation_rule_id="ESC-001",
        )


class TierLimitRule(_BaseRule):
    rule_id = "LIM-001-TIER-LIMITS"
    description = "The requested amount sits inside the tier's annual limit and sub-limit"
    category = RuleCategory.LIMITS

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        cost = ctx.request.estimated_cost_aed
        remaining = ctx.tier.annual_limit_aed - ctx.approved_amount_this_year_aed
        sub_limit = ctx.coverage.applicable_sub_limit_aed if ctx.coverage else None
        evidence = {
            "estimated_cost_aed": cost,
            "tier_annual_limit_aed": ctx.tier.annual_limit_aed,
            "approved_this_year_aed": ctx.approved_amount_this_year_aed,
            "remaining_annual_limit_aed": remaining,
            "applicable_sub_limit_aed": sub_limit,
            "currency": "AED",
        }
        sources = (self._tier_source(ctx, "Section 1 General"),)
        if cost > remaining:
            return self._escalate(
                ctx, "ESC-003",
                f"Requested AED {cost:,} exceeds the remaining annual limit of AED {remaining:,}",
                evidence, sources=sources, code="limits.annual_limit",
            )
        if sub_limit is not None and cost > sub_limit:
            return self._escalate(
                ctx, "ESC-003",
                f"Requested AED {cost:,} exceeds the AED {sub_limit:,} sub-limit for this benefit",
                evidence, sources=sources, code="limits.sub_limit",
            )
        return self._result(
            RuleOutcome.PASS, f"AED {cost:,} sits inside the tier limits", evidence, sources=sources
        )


class PreAuthorisationRequiredRule(_BaseRule):
    rule_id = "AUTH-001-PRE-AUTHORISATION-REQUIRED"
    description = "Whether this request needs pre-authorisation at all under the tier's threshold"
    category = RuleCategory.COVERAGE

    def evaluate(self, ctx: RuleContext) -> RuleResult:
        threshold = ctx.tier.pre_authorisation_threshold_aed
        required = bool(ctx.coverage and ctx.coverage.pre_authorisation_required) or (
            ctx.request.estimated_cost_aed >= threshold
        )
        evidence = {
            "pre_authorisation_required": required,
            "tier_threshold_aed": threshold,
            "estimated_cost_aed": ctx.request.estimated_cost_aed,
        }
        explanation = (
            "Pre-authorisation is required for this request"
            if required
            else f"Pre-authorisation is not required below the AED {threshold:,} threshold; the provider may bill directly"
        )
        return self._result(
            RuleOutcome.PASS, explanation, evidence,
            sources=self._coverage_source(ctx) or (self._tier_source(ctx, "Section 1 General"),),
        )


def build_uae_rules_engine() -> RuleSetEngine:
    return RuleSetEngine(
        name=RULESET_NAME,
        version=RULESET_VERSION,
        rules=[
            PolicyActiveRule(),
            WaitingPeriodRule(),
            ProviderDirectoryRule(),
            NetworkAccessRule(),
            SpecialtyRule(),
            ProcedureInScheduleRule(),
            TierCoverageRule(),
            DecisionClassRule(),
            RequiredDocumentsRule(),
            TierLimitRule(),
            PreAuthorisationRequiredRule(),
        ],
    )
