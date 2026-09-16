import pytest
from sqlalchemy import text

from preauth.application.commands import CloseCaseCommand, HumanDecisionCommand
from preauth.domain.enums import (
    CaseStatus,
    CloseReason,
    HumanDecisionType,
    RecommendationOutcome,
    ReviewQueue,
)
from preauth.domain.errors import (
    AuthorizationError,
    InvalidStateTransitionError,
    OperationNotAllowedError,
)
from tests.integration.helpers import (
    AGENT,
    DIRECTOR,
    OTHER_REVIEWER,
    REVIEWER,
    SYSTEM,
    case_in_review,
    complete_info,
    decide,
    evaluated_case,
    event_types,
)

D = HumanDecisionType


def _escalated_case(services):
    return case_in_review(
        services,
        complete_info(procedure_code="PROC-GENETIC-PANEL", diagnosis_code="Z80.3", conservative_treatment_weeks=None),
        docs=(),
    )


def _denial_case(services):
    return case_in_review(services, complete_info(conservative_treatment_weeks=2))


# --------------------------------------------------------------------------- routing


def test_request_review_routes_by_recommendation(services):
    case_id, rec = case_in_review(services)
    status = services.queries.get_status(case_id, AGENT)
    assert status.status is CaseStatus.PENDING_HUMAN_REVIEW and status.review_queue is ReviewQueue.CLINICAL_REVIEW

    escalated_id, rec = _escalated_case(services)
    assert rec.outcome is RecommendationOutcome.ESCALATE
    status = services.queries.get_status(escalated_id, AGENT)
    assert status.status is CaseStatus.ESCALATED
    assert status.review_queue is ReviewQueue.MEDICAL_DIRECTOR_REVIEW
    assert "CASE_ESCALATED" in event_types(services, escalated_id)


def test_request_review_requires_ready_recommendation(services):
    case_id, _ = evaluated_case(services, docs=())  # PENDING_INFORMATION
    with pytest.raises(OperationNotAllowedError):
        services.review.request_human_review(case_id, AGENT)


def test_review_queue_lists_expedited_first(services):
    standard, _ = case_in_review(services)
    expedited, _ = case_in_review(
        services, complete_info(patient={"member_id": "MBR-5005-01", "date_of_birth": "1979-09-09"},
                                policy_number="POL-000105", urgency="EXPEDITED")
    )
    queue = services.review.review_queue(REVIEWER, ReviewQueue.CLINICAL_REVIEW)
    assert [i.case_id for i in queue] == [expedited, standard]


# --------------------------------------------------------------------------- authority


@pytest.mark.parametrize("actor", [AGENT, SYSTEM])
def test_non_humans_cannot_record_decisions(services, actor):
    case_id, rec = case_in_review(services)
    command = HumanDecisionCommand(recommendation_id=rec.id, decision=D.APPROVE, rationale="Automated approval attempt")
    with pytest.raises(AuthorizationError) as exc:
        services.review.record_decision(case_id, actor, command)
    assert exc.value.code == "HUMAN_REVIEWER_REQUIRED"
    assert services.queries.get_status(case_id, AGENT).status is CaseStatus.PENDING_HUMAN_REVIEW


def test_system_cannot_finalise_even_through_transition_helper(services, seeded_session_factory, clock):
    """Bypassing the review service and calling the transition primitive directly is still refused."""
    from preauth.application.unit_of_work import UnitOfWork

    case_id, _ = case_in_review(services)
    with UnitOfWork(seeded_session_factory, clock) as uow:
        case = uow.cases.get(case_id)
        with pytest.raises(AuthorizationError):
            uow.transition(case, CaseStatus.APPROVED, SYSTEM, reason="attempt")


def test_decision_requires_assignment_to_that_reviewer(services):
    case_id, rec = case_in_review(services)
    command = HumanDecisionCommand(recommendation_id=rec.id, decision=D.APPROVE, rationale="Looks fine to me.")
    with pytest.raises(AuthorizationError) as exc:
        services.review.record_decision(case_id, REVIEWER, command)
    assert exc.value.code == "REVIEWER_NOT_ASSIGNED"
    services.review.assign_reviewer(case_id, OTHER_REVIEWER)
    with pytest.raises(AuthorizationError):
        services.review.record_decision(case_id, REVIEWER, command)


def test_escalated_cases_require_medical_director(services):
    case_id, _ = _escalated_case(services)
    with pytest.raises(AuthorizationError) as exc:
        services.review.assign_reviewer(case_id, REVIEWER)
    assert exc.value.code == "INSUFFICIENT_REVIEWER_ROLE"
    decision = decide(services, case_id, DIRECTOR, D.DENY)
    assert decision.to_status is CaseStatus.DENIED
    assert decision.is_override is False  # ESCALATE takes no position; resolving it is not an override


def test_stale_recommendation_rejected(services):
    case_id, _ = case_in_review(services)
    services.review.assign_reviewer(case_id, REVIEWER)
    command = HumanDecisionCommand(
        recommendation_id="00000000-0000-0000-0000-000000000000", decision=D.APPROVE, rationale="Approving an old one"
    )
    with pytest.raises(OperationNotAllowedError) as exc:
        services.review.record_decision(case_id, REVIEWER, command)
    assert exc.value.code == "STALE_RECOMMENDATION"


def test_rationale_is_mandatory():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HumanDecisionCommand(recommendation_id="0" * 36, decision=D.APPROVE, rationale="")


def test_decided_case_cannot_be_decided_again(services):
    case_id, _ = case_in_review(services)
    decide(services, case_id, REVIEWER, D.APPROVE)
    with pytest.raises(OperationNotAllowedError) as exc:
        decide(services, case_id, REVIEWER, D.DENY, assign=False)
    assert exc.value.code == "CASE_NOT_UNDER_REVIEW"


# --------------------------------------------------------------------------- outcomes


def test_human_approval_confirms_recommendation(services):
    case_id, rec = case_in_review(services)
    decision = decide(services, case_id, REVIEWER, D.APPROVE)
    assert decision.is_override is False and decision.reviewer_id == "rev-1"
    status = services.queries.get_status(case_id, AGENT)
    assert status.status is CaseStatus.APPROVED
    assert status.final_decision.id == decision.id and status.final_decision.decided_at is not None
    assert "RECOMMENDATION_OVERRIDDEN" not in event_types(services, case_id)


def test_human_override_preserves_system_recommendation(services, seeded_session_factory):
    case_id, rec = _denial_case(services)
    assert rec.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    decision = decide(services, case_id, REVIEWER, D.APPROVE)

    assert decision.is_override is True
    assert services.queries.get_status(case_id, AGENT).status is CaseStatus.APPROVED
    packet = services.review.review_packet(case_id, REVIEWER)
    assert packet.current_recommendation.id == rec.id
    assert packet.current_recommendation.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    assert packet.current_recommendation.rationale == rec.rationale
    assert [d.decision for d in packet.decisions] == [D.APPROVE]
    types = event_types(services, case_id)
    assert "RECOMMENDATION_GENERATED" in types and "RECOMMENDATION_OVERRIDDEN" in types

    # The database itself refuses to rewrite the recommendation.
    with pytest.raises(Exception, match="append-only"), seeded_session_factory() as s:
        s.execute(text("UPDATE recommendations SET outcome = 'RECOMMEND_APPROVAL' WHERE id = :id"), {"id": rec.id})
        s.commit()


def test_human_escalation_then_director_decision(services):
    case_id, _ = case_in_review(services)
    escalation = decide(services, case_id, REVIEWER, D.ESCALATE)
    assert escalation.to_status is CaseStatus.ESCALATED and escalation.is_override is True
    status = services.queries.get_status(case_id, AGENT)
    assert status.review_queue is ReviewQueue.MEDICAL_DIRECTOR_REVIEW
    assert services.queries.get_case(case_id, AGENT).assigned_reviewer_id is None
    with pytest.raises(InvalidStateTransitionError):
        decide(services, case_id, DIRECTOR, D.ESCALATE)
    final = decide(services, case_id, DIRECTOR, D.APPROVE)
    assert final.reviewer_role == "MEDICAL_DIRECTOR"
    packet = services.review.review_packet(case_id, DIRECTOR)
    assert [d.decision for d in packet.decisions] == [D.ESCALATE, D.APPROVE]


def test_human_request_for_information_reopens_intake(services):
    from preauth.application.commands import CaseInformationUpdate

    case_id, _ = case_in_review(services)
    decide(services, case_id, REVIEWER, D.REQUEST_INFORMATION)
    assert services.queries.get_status(case_id, AGENT).status is CaseStatus.PENDING_INFORMATION
    services.cases.update_information(case_id, AGENT, CaseInformationUpdate(clinical_summary="Additional history."))
    result = services.evaluation.submit_for_evaluation(case_id, AGENT)
    assert result.status is CaseStatus.RECOMMENDATION_READY
    assert len(services.review.review_packet(case_id, REVIEWER).recommendation_history) == 2


def test_close_after_decision(services):
    case_id, _ = case_in_review(services)
    decide(services, case_id, REVIEWER, D.DENY)
    with pytest.raises(AuthorizationError):
        services.cases.close_case(case_id, AGENT, CloseCaseCommand(reason=CloseReason.DECISION_COMMUNICATED))
    closed = services.cases.close_case(case_id, SYSTEM, CloseCaseCommand(reason=CloseReason.DECISION_COMMUNICATED))
    assert closed.status is CaseStatus.CLOSED
    assert services.queries.get_status(case_id, AGENT).final_decision.decision is D.DENY


def test_review_packet_contents_and_access(services):
    case_id, _ = case_in_review(services)
    packet = services.review.review_packet(case_id, REVIEWER)
    assert packet.case.provider.name == "Northgate Orthopaedic Clinic"
    assert packet.case.documents and packet.current_recommendation.rule_results
    assert packet.audit_history[0].event_type == "CASE_CREATED"
    with pytest.raises(AuthorizationError):
        services.review.review_packet(case_id, AGENT)
