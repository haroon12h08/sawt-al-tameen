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
from preauth.rules.model import MissingInformation, RuleContext, RuleResult


class RecommendationDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RecommendationOutcome
    rationale: str
    determining_rule_ids: tuple[str, ...]
    evidence: dict[str, Any]
    missing_information: tuple[MissingInformation, ...]
    engine_name: str
    engine_version: str


class RecommendationEngine(Protocol):
    name: str
    version: str

    def recommend(self, results: Sequence[RuleResult], ctx: RuleContext) -> RecommendationDraft: ...


class DeterministicRecommendationEngine:
    name = "deterministic-recommender"
    version = "1.0.0"

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
            rationale = (
                "Escalate for human review: information the insurer must resolve is unavailable. "
                + _describe(insurer_unknown)
            )
        elif unknown:
            outcome, determining = RecommendationOutcome.REQUEST_MORE_INFORMATION, unknown
            rationale = "Request more information from the provider: " + "; ".join(m.description for m in missing)
        else:
            outcome, determining = RecommendationOutcome.RECOMMEND_APPROVAL, list(results)
            rationale = f"Recommend approval. All {len(results)} rules passed."

        if ctx.coverage is not None and not ctx.coverage.preauth_required:
            rationale += " Note: coverage terms indicate pre-authorisation is not required for this procedure."

        return RecommendationDraft(
            outcome=outcome,
            rationale=rationale,
            determining_rule_ids=tuple(r.rule_id for r in determining),
            evidence={r.rule_id: {"outcome": r.outcome, **r.evidence} for r in results},
            # Missing information is only actionable when the recommendation asks for it or escalates on it.
            missing_information=missing if outcome is not RecommendationOutcome.RECOMMEND_APPROVAL else (),
            engine_name=self.name,
            engine_version=self.version,
        )


def _describe(results: Sequence[RuleResult]) -> str:
    return " ".join(f"[{r.rule_id}] {r.explanation}." for r in results)


def _dedupe_missing(results: Sequence[RuleResult]) -> tuple[MissingInformation, ...]:
    seen: dict[str, MissingInformation] = {}
    for r in results:
        for m in r.missing_information:
            seen.setdefault(m.code, m)
    return tuple(seen.values())
