import itertools

import pytest

from preauth.domain.case_state import (
    HUMAN_DECISION_STATES,
    TRANSITIONS,
    allowed_targets,
    assert_transition,
    is_transition_allowed,
)
from preauth.domain.enums import (
    ActorType,
    CaseStatus,
    HumanDecisionType,
    RecommendationOutcome,
)
from preauth.domain.errors import AuthorizationError, InvalidStateTransitionError
from preauth.domain.review import is_override, routing_status_for

S = CaseStatus


def test_valid_transition_passes():
    assert_transition(S.RECEIVED, S.INFORMATION_COLLECTION, ActorType.VOICE_AGENT)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (S.RECEIVED, S.APPROVED),
        (S.RECEIVED, S.RULE_EVALUATION),
        (S.INFORMATION_COLLECTION, S.PENDING_HUMAN_REVIEW),
        (S.RECOMMENDATION_READY, S.APPROVED),
        (S.CLOSED, S.INFORMATION_COLLECTION),
        (S.APPROVED, S.DENIED),
        (S.DENIED, S.PENDING_HUMAN_REVIEW),
        (S.ESCALATED, S.ESCALATED),
    ],
)
def test_invalid_transitions_are_rejected(src, dst):
    with pytest.raises(InvalidStateTransitionError) as exc:
        assert_transition(src, dst, ActorType.HUMAN_REVIEWER)
    assert exc.value.details["from_status"] == src
    assert exc.value.details["to_status"] == dst


def test_closed_is_terminal():
    assert allowed_targets(S.CLOSED) == frozenset()


@pytest.mark.parametrize("final", sorted(HUMAN_DECISION_STATES))
@pytest.mark.parametrize(
    "actor_type", [ActorType.SYSTEM, ActorType.VOICE_AGENT, ActorType.PROVIDER_PORTAL]
)
def test_non_human_actor_can_never_reach_final_decision(final, actor_type):
    for src in CaseStatus:
        assert not is_transition_allowed(src, final, actor_type)


def test_non_human_actor_gets_authorization_error_for_decision_transition():
    with pytest.raises(AuthorizationError) as exc:
        assert_transition(S.PENDING_HUMAN_REVIEW, S.APPROVED, ActorType.SYSTEM)
    assert exc.value.code == "TRANSITION_NOT_AUTHORIZED"


def test_every_transition_into_decision_state_is_human_only():
    for (src, dst), actors in TRANSITIONS.items():
        if dst in HUMAN_DECISION_STATES:
            assert actors == frozenset({ActorType.HUMAN_REVIEWER}), (src, dst)


def test_pipeline_internal_steps_are_system_only():
    for src, dst in [
        (S.VALIDATION, S.RULE_EVALUATION),
        (S.VALIDATION, S.PENDING_INFORMATION),
        (S.RULE_EVALUATION, S.RECOMMENDATION_READY),
    ]:
        assert TRANSITIONS[(src, dst)] == frozenset({ActorType.SYSTEM})


def test_no_self_transitions():
    for src, dst in TRANSITIONS:
        assert src != dst


def test_escalate_recommendation_routes_to_escalated_queue():
    assert routing_status_for(RecommendationOutcome.ESCALATE) is S.ESCALATED
    assert routing_status_for(RecommendationOutcome.RECOMMEND_DENIAL) is S.PENDING_HUMAN_REVIEW
    assert routing_status_for(RecommendationOutcome.RECOMMEND_APPROVAL) is S.PENDING_HUMAN_REVIEW


@pytest.mark.parametrize(
    ("rec", "decision", "expected"),
    [
        (RecommendationOutcome.RECOMMEND_APPROVAL, HumanDecisionType.APPROVE, False),
        (RecommendationOutcome.RECOMMEND_APPROVAL, HumanDecisionType.DENY, True),
        (RecommendationOutcome.RECOMMEND_DENIAL, HumanDecisionType.APPROVE, True),
        (RecommendationOutcome.RECOMMEND_DENIAL, HumanDecisionType.DENY, False),
        (RecommendationOutcome.RECOMMEND_DENIAL, HumanDecisionType.ESCALATE, True),
        (RecommendationOutcome.ESCALATE, HumanDecisionType.APPROVE, False),
        (RecommendationOutcome.ESCALATE, HumanDecisionType.DENY, False),
    ],
)
def test_override_detection(rec, decision, expected):
    assert is_override(rec, decision) is expected


def test_transition_table_is_exhaustively_checked():
    """Every (src, dst, actor) combination is either in the table or rejected."""
    for src, dst, actor in itertools.product(CaseStatus, CaseStatus, ActorType):
        allowed = actor in TRANSITIONS.get((src, dst), frozenset())
        assert is_transition_allowed(src, dst, actor) is allowed
