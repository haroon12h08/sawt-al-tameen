from preauth.domain.enums import CaseStatus, RecommendationOutcome
from preauth.seed.scenarios import CLINICAL_REVIEWER, run_scenarios


def test_demonstration_scenarios_reach_the_expected_outcomes(services):
    results = {r.name: r for r in run_scenarios(services)}
    assert results["complete_request_approved"].outcome == RecommendationOutcome.RECOMMEND_APPROVAL
    assert results["complete_request_approved"].status == CaseStatus.APPROVED

    assert results["missing_documentation"].outcome == RecommendationOutcome.REQUEST_MORE_INFORMATION
    assert results["missing_documentation"].status == CaseStatus.PENDING_INFORMATION

    assert results["tier_boundary_denial_recommended"].outcome == RecommendationOutcome.RECOMMEND_DENIAL
    assert results["tier_boundary_denial_recommended"].status == CaseStatus.PENDING_HUMAN_REVIEW

    assert results["ambiguous_escalated"].outcome == RecommendationOutcome.ESCALATE
    assert results["ambiguous_escalated"].status == CaseStatus.ESCALATED

    override = results["human_override_approved"]
    assert override.outcome == RecommendationOutcome.RECOMMEND_DENIAL
    assert override.status == CaseStatus.APPROVED


def test_override_scenario_keeps_both_records(services):
    results = {r.name: r for r in run_scenarios(services)}
    case_id = services.queries.find_case_id_by_reference(
        results["human_override_approved"].case_reference, CLINICAL_REVIEWER
    )
    packet = services.review.review_packet(case_id, CLINICAL_REVIEWER)
    assert packet.current_recommendation.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    assert packet.decisions[-1].is_override is True
