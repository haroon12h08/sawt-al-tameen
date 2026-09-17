"""Converts rule results into a recommendation. A recommendation is advisory; it is never a decision.

Precedence (first match wins):
  1. any FAIL                                        -> RECOMMEND_DENIAL
  2. any UNKNOWN that only the insurer can resolve   -> ESCALATE
  3. any UNKNOWN (provider can resolve)              -> REQUEST_MORE_INFORMATION
  4. all PASS                                        -> RECOMMEND_APPROVAL
An empty result set is treated as insufficient support and escalated.
"""

from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from preauth.domain.enums import MissingInformationSource, RecommendationOutcome, RuleOutcome
from preauth.rules.model import (
    EscalationRuleFacts,
    MissingInformation,
    RuleContext,
    RuleResult,
    SourceReference,
)


class RecommendationDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RecommendationOutcome
    rationale: str
    determining_rule_ids: tuple[str, ...]
    evidence: dict[str, Any]
    missing_information: tuple[MissingInformation, ...]
    sources: tuple["AttributedSource", ...]
    escalation_citations: tuple[EscalationRuleFacts, ...]
    engine_name: str
    engine_version: str


class AttributedSource(BaseModel):
    """A source document section together with the rules whose results relied on it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document: str
    section: str
    rule_ids: tuple[str, ...]


RecommendationDraft.model_rebuild()


class RecommendationEngine(Protocol):
    name: str
    version: str

    def recommend(self, results: Sequence[RuleResult], ctx: RuleContext) -> RecommendationDraft: ...


class DeterministicRecommendationEngine:
    name = "deterministic-recommender"
    # 1.1.0: recommendations carry source attribution.
    version = "1.1.0"

    def recommend(self, results: Sequence[RuleResult], ctx: RuleContext) -> RecommendationDraft:
        failed = [r for r in results if r.outcome is RuleOutcome.FAIL]
        unknown = [r for r in results if r.outcome is RuleOutcome.UNKNOWN]
        missing = _dedupe_missing(unknown)
        insurer_unknown = [
            r for r in unknown
            if any(m.source is MissingInformationSource.INSURER for m in r.missing_information)
        ]

        if not results:
            outcome, determining = RecommendationOutcome.ESCALATE, []
            rationale = "No rules were evaluated; the request cannot be supported automatically."
        elif failed:
            outcome, determining = RecommendationOutcome.RECOMMEND_DENIAL, failed
            rationale = "Recommend denial. Failed rules: " + _describe(failed)
            if unknown:
                rationale += f" Additionally, {len(unknown)} rule(s) could not be evaluated."
        elif insurer_unknown:
            outcome, determining = RecommendationOutcome.ESCALATE, insurer_unknown
            cited = ", ".join(sorted({r.escalation_rule_id for r in insurer_unknown if r.escalation_rule_id}))
            rationale = (
                f"Escalate for human review under {cited or 'the escalation rules'}. " + _describe(insurer_unknown)
            )
        elif unknown:
            outcome, determining = RecommendationOutcome.REQUEST_MORE_INFORMATION, unknown
            cited = ", ".join(sorted({r.escalation_rule_id for r in unknown if r.escalation_rule_id}))
            rationale = (
                f"Request more information from the provider ({cited}): " if cited
                else "Request more information from the provider: "
            ) + "; ".join(m.description for m in missing)
        else:
            outcome, determining = RecommendationOutcome.RECOMMEND_APPROVAL, list(results)
            rationale = f"Recommend approval. All {len(results)} rules passed."

        if ctx.coverage is not None and not ctx.coverage.pre_authorisation_required:
            rationale += (
                f" Note: pre-authorisation is not required for this procedure on the {ctx.tier.name} tier."
            )

        return RecommendationDraft(
            outcome=outcome,
            rationale=rationale,
            determining_rule_ids=tuple(r.rule_id for r in determining),
            evidence={r.rule_id: {"outcome": r.outcome, **r.evidence} for r in results},
            # Missing information is only actionable when the recommendation asks for it or escalates on it.
            missing_information=missing if outcome is not RecommendationOutcome.RECOMMEND_APPROVAL else (),
            sources=_attribute_sources(results),
            escalation_citations=_escalation_citations(results, ctx),
            engine_name=self.name,
            engine_version=self.version,
        )


def _escalation_citations(
    results: Sequence[RuleResult], ctx: RuleContext
) -> tuple[EscalationRuleFacts, ...]:
    """The ESC-### rules the determining results cited, with their text, so the reason is never generic."""
    seen: dict[str, EscalationRuleFacts] = {}
    for r in results:
        if r.escalation_rule_id and r.outcome is RuleOutcome.UNKNOWN:
            rule = ctx.escalation(r.escalation_rule_id)
            if rule is not None:
                seen.setdefault(rule.rule_id, rule)
    return tuple(seen.values())


def _attribute_sources(results: Sequence[RuleResult]) -> tuple[AttributedSource, ...]:
    by_source: dict[SourceReference, list[str]] = {}
    for r in results:
        for source in r.sources:
            by_source.setdefault(source, []).append(r.rule_id)
    return tuple(
        AttributedSource(document=s.document, section=s.section, rule_ids=tuple(ids)) for s, ids in by_source.items()
    )


def _describe(results: Sequence[RuleResult]) -> str:
    return " ".join(f"[{r.rule_id}] {r.explanation}." for r in results)


def _dedupe_missing(results: Sequence[RuleResult]) -> tuple[MissingInformation, ...]:
    seen: dict[str, MissingInformation] = {}
    for r in results:
        for m in r.missing_information:
            seen.setdefault(m.code, m)
    return tuple(seen.values())
