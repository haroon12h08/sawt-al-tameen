import pytest

from preauth.domain.enums import AuditEventType, CaseStatus, HumanDecisionType
from preauth.domain.errors import AuthorizationError
from preauth.infrastructure.observability import request_id_var
from preauth.rules.mock_ruleset import build_mock_rules_engine
from preauth.rules.model import RuleContext
from tests.integration.helpers import AGENT, REVIEWER, case_in_review, decide, evaluated_case

E = AuditEventType


def history(services, case_id):
    return services.queries.get_audit_history(case_id, REVIEWER)


def test_every_status_change_has_exactly_one_audit_event(services):
    case_id, _ = case_in_review(services)
    decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE)
    events = history(services, case_id)
    changes = [(e.data["from_status"], e.data["to_status"]) for e in events if e.event_type is E.CASE_STATUS_CHANGED]
    assert changes == [
        ("RECEIVED", "INFORMATION_COLLECTION"),
        ("INFORMATION_COLLECTION", "VALIDATION"),
        ("VALIDATION", "RULE_EVALUATION"),
        ("RULE_EVALUATION", "RECOMMENDATION_READY"),
        ("RECOMMENDATION_READY", "PENDING_HUMAN_REVIEW"),
        ("PENDING_HUMAN_REVIEW", "APPROVED"),
    ]
    # The chain is contiguous: each change starts where the previous ended.
    for (_, prev_to), (next_from, _) in zip(changes, changes[1:]):
        assert prev_to == next_from


def test_full_event_sequence_for_approved_case(services):
    case_id, _ = case_in_review(services)
    decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE)
    assert [e.event_type for e in history(services, case_id)] == [
        E.CASE_CREATED,
        E.INFORMATION_COLLECTED,
        E.CASE_STATUS_CHANGED,
        E.DOCUMENT_REGISTERED,
        E.CASE_STATUS_CHANGED,
        E.VALIDATION_PASSED,
        E.CASE_STATUS_CHANGED,
        E.RULES_EVALUATED,
        E.RECOMMENDATION_GENERATED,
        E.CASE_STATUS_CHANGED,
        E.CASE_STATUS_CHANGED,
        E.HUMAN_REVIEW_REQUESTED,
        E.REVIEWER_ASSIGNED,
        E.CASE_STATUS_CHANGED,
        E.HUMAN_DECISION_RECORDED,
    ]


def test_sequences_are_contiguous_and_timestamps_ordered(services):
    case_id, _ = case_in_review(services)
    events = history(services, case_id)
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert all(a.occurred_at <= b.occurred_at for a, b in zip(events, events[1:]))
    assert len({e.id for e in events}) == len(events)


def test_actor_attribution(services):
    case_id, _ = case_in_review(services)
    decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE)
    events = history(services, case_id)
    by_type = {e.event_type: e for e in events}
    assert by_type[E.CASE_CREATED].actor_type == "VOICE_AGENT"
    assert by_type[E.RULES_EVALUATED].actor_type == "SYSTEM"
    assert by_type[E.RECOMMENDATION_GENERATED].actor_type == "SYSTEM"
    decision = by_type[E.HUMAN_DECISION_RECORDED]
    assert decision.actor_type == "HUMAN_REVIEWER" and decision.actor_id == "rev-1"
    assert decision.data["rationale"] and decision.data["recommendation"] == "RECOMMEND_APPROVAL"
    system_changes = [e for e in events if e.event_type is E.CASE_STATUS_CHANGED and e.actor_type == "SYSTEM"]
    assert all(e.data["triggered_by"]["actor_id"] == AGENT.id for e in system_changes)


def test_request_id_is_recorded(services):
    token = request_id_var.set("req-abc-123")
    try:
        case_id, _ = evaluated_case(services)
    finally:
        request_id_var.reset(token)
    assert {e.request_id for e in history(services, case_id)} == {"req-abc-123"}


def test_failed_operation_leaves_no_partial_audit(services):
    case_id, rec = case_in_review(services)
    before = history(services, case_id)
    with pytest.raises(AuthorizationError):
        decide(services, case_id, AGENT, HumanDecisionType.APPROVE, recommendation_id=rec.id, assign=False)
    assert history(services, case_id) == before


def test_evaluation_can_be_reproduced_from_stored_snapshot(services, seeded_session_factory):
    from sqlalchemy import select

    from preauth.infrastructure.db.models import RuleEvaluation

    case_id, result = evaluated_case(services, docs=())
    with seeded_session_factory() as s:
        stored = s.scalars(select(RuleEvaluation).where(RuleEvaluation.case_id == case_id)).one()
        snapshot, version = stored.input_snapshot, stored.engine_version
    engine = build_mock_rules_engine()
    assert engine.version == version
    replayed = engine.evaluate(RuleContext.model_validate(snapshot))
    assert [(r.rule_id, r.outcome) for r in replayed.results] == [
        (r.rule_id, r.outcome) for r in result.recommendation.rule_results
    ]


def test_audit_history_restricted_to_reviewers(services):
    case_id, _ = evaluated_case(services)
    with pytest.raises(AuthorizationError):
        services.queries.get_audit_history(case_id, AGENT)
    assert services.queries.get_status(case_id, AGENT).status is CaseStatus.RECOMMENDATION_READY
