from preauth.domain.enums import (
    DocumentType,
    MissingInformationSource,
    PolicyStatus,
    RecommendationOutcome,
    RuleOutcome,
)
from preauth.recommendation.engine import DeterministicRecommendationEngine
from preauth.rules.mock_ruleset import build_mock_rules_engine
from preauth.rules.model import MissingInformation, RuleResult
from tests.unit.context_factory import make_context

RULES = build_mock_rules_engine()
RECOMMENDER = DeterministicRecommendationEngine()
R = RecommendationOutcome


def recommend(ctx):
    return RECOMMENDER.recommend(RULES.evaluate(ctx).results, ctx)


def test_all_pass_recommends_approval():
    draft = recommend(make_context())
    assert draft.outcome is R.RECOMMEND_APPROVAL
    assert draft.missing_information == ()
    assert len(draft.determining_rule_ids) == 8
    assert draft.engine_name == "deterministic-recommender" and draft.engine_version


def test_missing_documents_requests_more_information():
    draft = recommend(make_context(documents=(DocumentType.CLINICAL_NOTES,)))
    assert draft.outcome is R.REQUEST_MORE_INFORMATION
    assert [m.code for m in draft.missing_information] == ["document.IMAGING_REPORT"]
    assert draft.determining_rule_ids == ("DOC-001-REQUIRED-DOCUMENTS",)


def test_failure_recommends_denial():
    draft = recommend(make_context(policy_status=PolicyStatus.LAPSED))
    assert draft.outcome is R.RECOMMEND_DENIAL
    assert draft.determining_rule_ids == ("ELIG-001-POLICY-ACTIVE",)
    assert "ELIG-001-POLICY-ACTIVE" in draft.rationale


def test_failure_takes_precedence_over_missing_information():
    draft = recommend(make_context(policy_status=PolicyStatus.LAPSED, documents=()))
    assert draft.outcome is R.RECOMMEND_DENIAL
    assert draft.missing_information  # still reported for the reviewer


def test_insurer_knowledge_gap_escalates():
    draft = recommend(make_context(coverage=None))
    assert draft.outcome is R.ESCALATE
    assert all(m.source is MissingInformationSource.INSURER for m in draft.missing_information)


def test_insurer_gap_takes_precedence_over_provider_gap():
    ctx = make_context(documents=())
    results = list(RULES.evaluate(ctx).results)
    results.append(
        RuleResult(
            rule_id="X", rule_version="1", outcome=RuleOutcome.UNKNOWN, explanation="gap", evidence={},
            missing_information=(
                MissingInformation(code="insurer.x", description="x", source=MissingInformationSource.INSURER),
            ),
        )
    )
    assert RECOMMENDER.recommend(results, ctx).outcome is R.ESCALATE


def test_empty_results_escalate_rather_than_approve():
    assert RECOMMENDER.recommend([], make_context()).outcome is R.ESCALATE


def test_single_fail_among_passes_never_approves():
    ctx = make_context()
    results = list(RULES.evaluate(ctx).results)
    results[3] = results[3].model_copy(update={"outcome": RuleOutcome.FAIL})
    assert RECOMMENDER.recommend(results, ctx).outcome is R.RECOMMEND_DENIAL


def test_evidence_preserves_every_rule_outcome():
    draft = recommend(make_context(conservative_weeks=2))
    assert draft.evidence["MED-002-CONSERVATIVE-TREATMENT"]["outcome"] is RuleOutcome.FAIL
    assert draft.evidence["ELIG-001-POLICY-ACTIVE"]["outcome"] is RuleOutcome.PASS


def test_recommendation_is_deterministic():
    ctx = make_context(documents=())
    assert recommend(ctx) == recommend(ctx)


def test_preauth_not_required_is_noted():
    assert "not required" in recommend(make_context(preauth_required=False)).rationale
