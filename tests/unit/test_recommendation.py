from datetime import date

from preauth.domain.enums import DecisionClass, DirectoryStatus, PolicyStatus, RecommendationOutcome, RuleOutcome
from preauth.recommendation.engine import DeterministicRecommendationEngine
from preauth.rules.uae_ruleset import build_uae_rules_engine
from tests.unit.context_factory import make_context

RULES = build_uae_rules_engine()
RECOMMENDER = DeterministicRecommendationEngine()
R = RecommendationOutcome


def recommend(ctx):
    return RECOMMENDER.recommend(RULES.evaluate(ctx).results, ctx)


def test_all_pass_recommends_approval():
    draft = recommend(make_context())
    assert draft.outcome is R.RECOMMEND_APPROVAL
    assert draft.missing_information == () and draft.escalation_citations == ()
    assert draft.engine_name == "deterministic-recommender"


def test_missing_documents_requests_more_information():
    draft = recommend(make_context(documents=()))
    assert draft.outcome is R.REQUEST_MORE_INFORMATION
    assert [m.code for m in draft.missing_information] == ["document.CLINICAL_NOTES"]
    assert "ESC-001" in draft.rationale


def test_tier_exclusion_recommends_denial():
    draft = recommend(make_context(covered=False))
    assert draft.outcome is R.RECOMMEND_DENIAL
    assert draft.determining_rule_ids == ("COV-002-TIER-COVERS-PROCEDURE",)


def test_lapsed_policy_recommends_denial():
    assert recommend(make_context(policy_status=PolicyStatus.LAPSED)).outcome is R.RECOMMEND_DENIAL


def test_ambiguous_procedure_escalates_and_cites_the_rule():
    draft = recommend(make_context(decision_class=DecisionClass.AMBIGUOUS, escalation_rule_id="ESC-006"))
    assert draft.outcome is R.ESCALATE
    assert [c.rule_id for c in draft.escalation_citations] == ["ESC-006"]
    assert draft.escalation_citations[0].situation
    assert "ESC-006" in draft.rationale


def test_amount_over_limit_escalates_with_esc_003():
    draft = recommend(make_context(cost=5_000_000))
    assert draft.outcome is R.ESCALATE
    assert "ESC-003" in [c.rule_id for c in draft.escalation_citations]


def test_waiting_period_escalates_with_esc_007():
    draft = recommend(
        make_context(category="maternity", waiting_months=12, policy_start=date(2026, 8, 1))
    )
    assert draft.outcome is R.ESCALATE
    assert "ESC-007" in [c.rule_id for c in draft.escalation_citations]


def test_inactive_provider_escalates_with_esc_008():
    draft = recommend(make_context(directory_status=DirectoryStatus.SUSPENDED))
    assert draft.outcome is R.ESCALATE
    assert "ESC-008" in [c.rule_id for c in draft.escalation_citations]


def test_failure_takes_precedence_over_escalation_and_missing_information():
    draft = recommend(make_context(covered=False, documents=(), cost=5_000_000))
    assert draft.outcome is R.RECOMMEND_DENIAL


def test_escalation_takes_precedence_over_missing_documents():
    draft = recommend(make_context(decision_class=DecisionClass.AMBIGUOUS, escalation_rule_id="ESC-002", documents=()))
    assert draft.outcome is R.ESCALATE


def test_single_fail_among_passes_never_approves():
    ctx = make_context()
    results = list(RULES.evaluate(ctx).results)
    results[3] = results[3].model_copy(update={"outcome": RuleOutcome.FAIL})
    assert RECOMMENDER.recommend(results, ctx).outcome is R.RECOMMEND_DENIAL


def test_empty_results_escalate_rather_than_approve():
    assert RECOMMENDER.recommend([], make_context()).outcome is R.ESCALATE


def test_recommendation_cites_schedule_sources():
    draft = recommend(make_context())
    documents = {s.document for s in draft.sources}
    assert any("Schedule of Benefits" in d for d in documents)
    assert "Membership and policy register" in documents
    assert all(s.rule_ids for s in draft.sources)


def test_pre_authorisation_not_required_is_noted():
    draft = recommend(make_context(pre_auth_required=False, tier_id="EXECUTIVE", cost=500, documents=()))
    assert "not required" in draft.rationale


def test_recommendation_is_deterministic():
    ctx = make_context(documents=())
    assert recommend(ctx) == recommend(ctx)
