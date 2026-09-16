from preauth.domain.enums import CaseStatus, RecommendationOutcome
from preauth.seed.scenarios import CLINICAL_REVIEWER, run_scenarios
from tests.integration.conftest import TODAY


def test_all_demonstration_scenarios_reach_expected_outcomes(services):
    results = {r.name: r.case_id for r in run_scenarios(services, TODAY)}
    q, reviewer = services.queries, CLINICAL_REVIEWER

    def status(name):
        return q.get_status(results[name], reviewer).status

    def recommendation(name):
        return q.get_latest_recommendation(results[name], reviewer).outcome

    assert status("complete_request_approved") is CaseStatus.APPROVED
    assert recommendation("complete_request_approved") is RecommendationOutcome.RECOMMEND_APPROVAL

    assert status("missing_documentation") is CaseStatus.PENDING_INFORMATION
    assert recommendation("missing_documentation") is RecommendationOutcome.REQUEST_MORE_INFORMATION

    assert status("rule_failure_denial_recommended") is CaseStatus.PENDING_HUMAN_REVIEW
    assert recommendation("rule_failure_denial_recommended") is RecommendationOutcome.RECOMMEND_DENIAL

    assert status("insufficient_knowledge_escalated") is CaseStatus.ESCALATED
    assert recommendation("insufficient_knowledge_escalated") is RecommendationOutcome.ESCALATE

    override = q.get_status(results["human_override_approved"], reviewer)
    assert override.status is CaseStatus.APPROVED
    assert override.final_decision.is_override is True
    assert recommendation("human_override_approved") is RecommendationOutcome.RECOMMEND_DENIAL
