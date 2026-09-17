"""Human review: the only path to APPROVED or DENIED."""

import pytest

from preauth.application.commands import CloseCaseCommand, HumanDecisionCommand
from preauth.domain.enums import (
    CallOutcome,
    CaseStatus,
    CloseReason,
    HumanDecisionType,
    RecommendationOutcome,
    ReviewQueue,
)
from preauth.domain.errors import AuthorizationError, InvalidStateTransitionError, OperationNotAllowedError
from tests.integration.helpers import (
    AGENT,
    BARIATRIC_HOSPITAL,
    BASIC_MEMBER,
    COMPREHENSIVE_MEMBER,
    DIRECTOR,
    OTHER_REVIEWER,
    REVIEWER,
    SYSTEM,
    approved_case,
    check,
    decide,
    event_types,
    log,
    verify,
)

D = HumanDecisionType


def denial_case(services):
    verification = verify(services, member=BASIC_MEMBER)
    return check(services, verification, procedure_code="SP-20050", cost=62000)


def escalated_case(services):
    verification = verify(
        services, provider=BARIATRIC_HOSPITAL, member=COMPREHENSIVE_MEMBER, organisation="Yas Horizon"
    )
    return check(services, verification, procedure_code="SP-20110", cost=48000)


# --------------------------------------------------------------------------- authority


@pytest.mark.parametrize("actor", [AGENT, SYSTEM])
def test_non_humans_cannot_record_decisions(services, actor):
    _, result = approved_case(services)
    recommendation = services.queries.get_latest_recommendation(result.case_id, REVIEWER)
    command = HumanDecisionCommand(
        recommendation_id=recommendation.id, decision=D.APPROVE, rationale="Automated approval attempt"
    )
    with pytest.raises(AuthorizationError) as exc:
        services.review.record_decision(result.case_id, actor, command)
    assert exc.value.code == "HUMAN_REVIEWER_REQUIRED"
    assert services.queries.get_status(result.case_id, AGENT).status is CaseStatus.PENDING_HUMAN_REVIEW


def test_system_cannot_finalise_through_the_transition_primitive(services, seeded_session_factory, clock):
    from preauth.application.unit_of_work import UnitOfWork

    _, result = approved_case(services)
    with UnitOfWork(seeded_session_factory, clock) as uow:
        case = uow.cases.get(result.case_id)
        with pytest.raises(AuthorizationError):
            uow.transition(case, CaseStatus.APPROVED, SYSTEM, reason="attempt")


def test_decision_requires_assignment_to_that_reviewer(services):
    _, result = approved_case(services)
    recommendation = services.queries.get_latest_recommendation(result.case_id, REVIEWER)
    command = HumanDecisionCommand(
        recommendation_id=recommendation.id, decision=D.APPROVE, rationale="Looks fine to me."
    )
    with pytest.raises(AuthorizationError) as exc:
        services.review.record_decision(result.case_id, REVIEWER, command)
    assert exc.value.code == "REVIEWER_NOT_ASSIGNED"
    services.review.assign_reviewer(result.case_id, OTHER_REVIEWER)
    with pytest.raises(AuthorizationError):
        services.review.record_decision(result.case_id, REVIEWER, command)


def test_escalated_cases_require_the_medical_director(services):
    result = escalated_case(services)
    assert services.queries.get_status(result.case_id, AGENT).review_queue is ReviewQueue.MEDICAL_DIRECTOR_REVIEW
    with pytest.raises(AuthorizationError) as exc:
        services.review.assign_reviewer(result.case_id, REVIEWER)
    assert exc.value.code == "INSUFFICIENT_REVIEWER_ROLE"
    decision = decide(services, result.case_id, DIRECTOR, D.DENY)
    assert decision.to_status is CaseStatus.DENIED
    assert decision.is_override is False  # ESCALATE takes no position


def test_stale_recommendation_is_rejected(services):
    _, result = approved_case(services)
    services.review.assign_reviewer(result.case_id, REVIEWER)
    with pytest.raises(OperationNotAllowedError) as exc:
        services.review.record_decision(
            result.case_id,
            REVIEWER,
            HumanDecisionCommand(
                recommendation_id="00000000-0000-4000-8000-000000000000",
                decision=D.APPROVE,
                rationale="Approving an old one",
            ),
        )
    assert exc.value.code == "STALE_RECOMMENDATION"


def test_decided_case_cannot_be_decided_again(services):
    _, result = approved_case(services)
    decide(services, result.case_id, REVIEWER, D.APPROVE)
    with pytest.raises(OperationNotAllowedError) as exc:
        decide(services, result.case_id, REVIEWER, D.DENY, assign=False)
    assert exc.value.code == "CASE_NOT_UNDER_REVIEW"


# --------------------------------------------------------------------------- outcomes


def test_human_approval_confirms_the_recommendation(services):
    _, result = approved_case(services)
    decision = decide(services, result.case_id, REVIEWER, D.APPROVE)
    assert decision.is_override is False and decision.reviewer_id == "rev-1"
    status = services.queries.get_status(result.case_id, AGENT)
    assert status.status is CaseStatus.APPROVED and status.final_decision.id == decision.id
    assert "RECOMMENDATION_OVERRIDDEN" not in event_types(services, result.case_id)


def test_override_preserves_the_system_recommendation(services, seeded_session_factory):
    from sqlalchemy import text

    result = denial_case(services)
    recommendation = services.queries.get_latest_recommendation(result.case_id, REVIEWER)
    assert recommendation.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    decision = decide(services, result.case_id, REVIEWER, D.APPROVE)
    assert decision.is_override is True

    packet = services.review.review_packet(result.case_id, REVIEWER)
    assert packet.current_recommendation.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    assert packet.current_recommendation.rationale == recommendation.rationale
    assert [d.decision for d in packet.decisions] == [D.APPROVE]
    assert "RECOMMENDATION_OVERRIDDEN" in event_types(services, result.case_id)

    with pytest.raises(Exception, match="append-only"), seeded_session_factory() as s:
        s.execute(
            text("UPDATE recommendations SET outcome = 'RECOMMEND_APPROVAL' WHERE id = :id"),
            {"id": recommendation.id},
        )
        s.commit()


def test_escalation_by_a_reviewer_moves_to_the_director_queue(services):
    _, result = approved_case(services)
    escalation = decide(services, result.case_id, REVIEWER, D.ESCALATE)
    assert escalation.to_status is CaseStatus.ESCALATED and escalation.is_override is True
    assert services.queries.get_case(result.case_id, AGENT).assigned_reviewer_id is None
    with pytest.raises(InvalidStateTransitionError):
        decide(services, result.case_id, DIRECTOR, D.ESCALATE)
    final = decide(services, result.case_id, DIRECTOR, D.APPROVE)
    assert final.reviewer_role == "MEDICAL_DIRECTOR"


def test_request_for_information_reopens_the_case(services):
    _, result = approved_case(services)
    decide(services, result.case_id, REVIEWER, D.REQUEST_INFORMATION)
    assert services.queries.get_status(result.case_id, AGENT).status is CaseStatus.PENDING_INFORMATION


def test_review_packet_contains_the_full_picture(services):
    verification, result = approved_case(services)
    log(
        services,
        outcome=CallOutcome.RECOMMENDATION_PREPARED,
        case_reference=result.case_reference,
        verification=verification,
        summary="Arthroscopy requested; recommendation prepared for sign-off.",
    )
    packet = services.review.review_packet(result.case_id, REVIEWER)
    assert packet.case.provider.name == "Al Hudaiba Crescent Hospital"
    assert packet.case.member.tier.tier_id == "EXECUTIVE"
    assert packet.case.procedure.procedure_code == "SP-20040"
    assert packet.current_recommendation.rule_results
    assert packet.call_logs and packet.call_logs[0].reference.startswith("CL-")
    assert packet.audit_history[0].event_type == "CASE_CREATED"
    with pytest.raises(AuthorizationError):
        services.review.review_packet(result.case_id, AGENT)


def test_review_queue_lists_cases_awaiting_a_human(services):
    _, first = approved_case(services)
    queue = services.review.review_queue(REVIEWER, ReviewQueue.CLINICAL_REVIEW)
    assert first.case_id in [item.case_id for item in queue]
    assert queue[0].estimated_cost_aed == 21000


def test_close_after_decision(services):
    _, result = approved_case(services)
    decide(services, result.case_id, REVIEWER, D.DENY)
    closed = services.cases.close_case(
        result.case_id, SYSTEM, CloseCaseCommand(reason=CloseReason.DECISION_COMMUNICATED)
    )
    assert closed.status is CaseStatus.CLOSED
