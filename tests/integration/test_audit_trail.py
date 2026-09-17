"""The audit trail is part of the domain, not the logs."""

import pytest

from preauth.domain.enums import AuditEventType, CallOutcome, CaseStatus, HumanDecisionType
from preauth.domain.errors import AuthorizationError
from preauth.infrastructure.observability import request_id_var
from preauth.rules.model import RuleContext
from preauth.rules.uae_ruleset import build_uae_rules_engine
from tests.integration.helpers import AGENT, REVIEWER, approved_case, check, decide, log, verify

E = AuditEventType


def history(services, case_id):
    return services.queries.get_audit_history(case_id, REVIEWER)


def test_full_event_sequence_for_an_approved_case(services):
    verification, result = approved_case(services)
    log(
        services, outcome=CallOutcome.RECOMMENDATION_PREPARED, case_reference=result.case_reference,
        verification=verification, summary="Recommendation prepared for sign-off.",
    )
    decide(services, result.case_id, REVIEWER, HumanDecisionType.APPROVE)
    types = [e.event_type for e in history(services, result.case_id)]
    assert types[0] is E.CASE_CREATED
    for expected in (
        E.INFORMATION_COLLECTED, E.DOCUMENT_REGISTERED, E.VALIDATION_PASSED, E.RULES_EVALUATED,
        E.RECOMMENDATION_GENERATED, E.HUMAN_REVIEW_REQUESTED, E.CALL_SUMMARY_LOGGED, E.REVIEWER_ASSIGNED,
        E.HUMAN_DECISION_RECORDED,
    ):
        assert expected in types, expected
    assert types[-1] is E.HUMAN_DECISION_RECORDED


def test_status_changes_form_a_contiguous_chain(services):
    _, result = approved_case(services)
    decide(services, result.case_id, REVIEWER, HumanDecisionType.APPROVE)
    changes = [
        (e.data["from_status"], e.data["to_status"])
        for e in history(services, result.case_id)
        if e.event_type is E.CASE_STATUS_CHANGED
    ]
    assert changes[0][0] == CaseStatus.RECEIVED
    assert changes[-1][1] == CaseStatus.APPROVED
    for (_, previous_to), (next_from, _) in zip(changes, changes[1:]):
        assert previous_to == next_from


def test_sequences_are_contiguous_and_ordered(services):
    _, result = approved_case(services)
    events = history(services, result.case_id)
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
    assert all(a.occurred_at <= b.occurred_at for a, b in zip(events, events[1:]))


def test_actor_attribution(services):
    _, result = approved_case(services)
    decide(services, result.case_id, REVIEWER, HumanDecisionType.APPROVE)
    by_type = {e.event_type: e for e in history(services, result.case_id)}
    assert by_type[E.CASE_CREATED].actor_type == "VOICE_AGENT"
    assert by_type[E.RULES_EVALUATED].actor_type == "SYSTEM"
    decision = by_type[E.HUMAN_DECISION_RECORDED]
    assert decision.actor_type == "HUMAN_REVIEWER" and decision.actor_id == "rev-1"
    assert decision.data["rationale"]


def test_evaluation_records_the_escalation_rules_it_cited(services):
    from tests.integration.helpers import BARIATRIC_HOSPITAL, COMPREHENSIVE_MEMBER

    verification = verify(
        services, provider=BARIATRIC_HOSPITAL, member=COMPREHENSIVE_MEMBER, organisation="Yas Horizon"
    )
    result = check(services, verification, procedure_code="SP-20110", cost=48000)
    evaluated = next(e for e in history(services, result.case_id) if e.event_type is E.RULES_EVALUATED)
    assert "ESC-001" in evaluated.data["escalation_rule_ids"]
    generated = next(e for e in history(services, result.case_id) if e.event_type is E.RECOMMENDATION_GENERATED)
    assert "ESC-001" in generated.data["escalation_rule_ids"]


def test_request_id_is_recorded(services):
    token = request_id_var.set("req-trace-7")
    try:
        _, result = approved_case(services)
    finally:
        request_id_var.reset(token)
    assert {e.request_id for e in history(services, result.case_id)} == {"req-trace-7"}


def test_failed_operation_leaves_no_partial_audit(services):
    _, result = approved_case(services)
    before = history(services, result.case_id)
    with pytest.raises(AuthorizationError):
        decide(services, result.case_id, AGENT, HumanDecisionType.APPROVE, assign=False)
    assert history(services, result.case_id) == before


def test_evaluation_is_reproducible_from_the_stored_snapshot(services, seeded_session_factory):
    from sqlalchemy import select

    from preauth.infrastructure.db.models import RuleEvaluation

    _, result = approved_case(services)
    with seeded_session_factory() as session:
        stored = session.scalars(
            select(RuleEvaluation).where(RuleEvaluation.case_id == result.case_id)
        ).all()[-1]
        snapshot, version = stored.input_snapshot, stored.engine_version

    engine = build_uae_rules_engine()
    assert engine.version == version
    replayed = engine.evaluate(RuleContext.model_validate(snapshot))
    recommendation = services.queries.get_latest_recommendation(result.case_id, REVIEWER)
    assert [(r.rule_id, r.outcome) for r in replayed.results] == [
        (r.rule_id, r.outcome) for r in recommendation.rule_results
    ]


def test_audit_history_is_restricted_to_reviewers(services):
    _, result = approved_case(services)
    with pytest.raises(AuthorizationError):
        services.queries.get_audit_history(result.case_id, AGENT)
