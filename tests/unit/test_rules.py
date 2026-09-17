from datetime import date

import pytest
from pydantic import ValidationError

from preauth.domain.enums import (
    DecisionClass,
    DirectoryStatus,
    MissingInformationSource,
    PolicyStatus,
    RuleOutcome,
    Urgency,
)
from preauth.domain.errors import IntegrityViolationError
from preauth.rules.engine import RuleSetEngine
from preauth.rules.model import MissingInformation, RuleContext, RuleResult
from preauth.rules.uae_ruleset import build_uae_rules_engine
from tests.unit.context_factory import make_context

ENGINE = build_uae_rules_engine()


def outcomes(ctx) -> dict[str, RuleOutcome]:
    return {r.rule_id: r.outcome for r in ENGINE.evaluate(ctx).results}


def result_for(ctx, rule_id) -> RuleResult:
    return next(r for r in ENGINE.evaluate(ctx).results if r.rule_id == rule_id)


def test_complete_request_passes_every_rule():
    assert set(outcomes(make_context()).values()) == {RuleOutcome.PASS}


def test_engine_reports_every_rule_with_version():
    evaluation = ENGINE.evaluate(make_context())
    assert evaluation.engine_name == "uae-preauth-ruleset"
    assert len(evaluation.results) == len(ENGINE.rules) == 11
    assert all(r.rule_version for r in evaluation.results)


# --------------------------------------------------------------------------- eligibility


def test_lapsed_policy_fails():
    result = result_for(make_context(policy_status=PolicyStatus.LAPSED), "ELIG-001-POLICY-ACTIVE")
    assert result.outcome is RuleOutcome.FAIL
    assert "lapsed" in result.explanation


def test_treatment_after_renewal_fails():
    ctx = make_context(policy_renewal=date(2026, 9, 30), treatment_date=date(2026, 10, 8))
    assert outcomes(ctx)["ELIG-001-POLICY-ACTIVE"] is RuleOutcome.FAIL


def test_waiting_period_escalates_with_esc_007():
    ctx = make_context(category="maternity", waiting_months=12, policy_start=date(2026, 6, 1))
    result = result_for(ctx, "ELIG-002-WAITING-PERIOD")
    assert result.outcome is RuleOutcome.UNKNOWN
    assert result.escalation_rule_id == "ESC-007"
    assert result.missing_information[0].source is MissingInformationSource.INSURER


def test_emergency_waives_the_waiting_period():
    ctx = make_context(
        category="emergency", waiting_months=12, policy_start=date(2026, 6, 1), urgency=Urgency.EXPEDITED
    )
    assert outcomes(ctx)["ELIG-002-WAITING-PERIOD"] is RuleOutcome.PASS


# --------------------------------------------------------------------------- network


@pytest.mark.parametrize("status", [DirectoryStatus.SUSPENDED, DirectoryStatus.PENDING_ONBOARDING])
def test_inactive_provider_escalates_with_esc_008(status):
    result = result_for(make_context(directory_status=status), "NET-001-PROVIDER-DIRECTORY")
    assert result.outcome is RuleOutcome.UNKNOWN and result.escalation_rule_id == "ESC-008"


@pytest.mark.parametrize(
    ("tier_id", "provider_rank", "expected"),
    [
        ("BASIC", 1, RuleOutcome.PASS),          # basic provider, basic member
        ("EXECUTIVE", 1, RuleOutcome.PASS),      # networks nest upwards
        ("COMPREHENSIVE", 2, RuleOutcome.PASS),
        ("BASIC", 3, RuleOutcome.FAIL),          # comprehensive-only provider, basic member
        ("ENHANCED", 4, RuleOutcome.FAIL),
        ("EXECUTIVE", 4, RuleOutcome.PASS),
    ],
)
def test_network_nesting(tier_id, provider_rank, expected):
    ctx = make_context(tier_id=tier_id, provider_network_rank=provider_rank)
    assert outcomes(ctx)["NET-002-NETWORK-ACCESS"] is expected


def test_executive_tier_covers_out_of_network():
    ctx = make_context(tier_id="EXECUTIVE", provider_network_rank=4)
    assert outcomes(ctx)["NET-002-NETWORK-ACCESS"] is RuleOutcome.PASS


def test_specialty_gap_escalates_rather_than_denying():
    """A credentialing gap is a network matter; denying the benefit would be wrong."""
    result = result_for(make_context(provider_specialties=("Dentistry",)), "NET-003-PROVIDER-SPECIALTY")
    assert result.outcome is RuleOutcome.UNKNOWN and result.escalation_rule_id == "ESC-008"


# --------------------------------------------------------------------------- coverage


def test_procedure_missing_from_schedule_escalates_with_esc_005():
    result = result_for(make_context(procedure=None, coverage=None), "COV-001-PROCEDURE-IN-SCHEDULE")
    assert result.outcome is RuleOutcome.UNKNOWN and result.escalation_rule_id == "ESC-005"


def test_tier_does_not_cover_procedure_fails():
    result = result_for(make_context(covered=False), "COV-002-TIER-COVERS-PROCEDURE")
    assert result.outcome is RuleOutcome.FAIL
    assert "Executive" in result.explanation


def test_ambiguous_procedure_escalates_with_its_own_rule():
    ctx = make_context(decision_class=DecisionClass.AMBIGUOUS, escalation_rule_id="ESC-003")
    result = result_for(ctx, "COV-003-SCHEDULE-DECIDABLE")
    assert result.outcome is RuleOutcome.UNKNOWN and result.escalation_rule_id == "ESC-003"
    assert result.evidence["escalation_rule"]["situation"].startswith("Situation for ESC-003")


# --------------------------------------------------------------------------- documents, limits, threshold


def test_missing_documents_are_unknown_not_fail():
    result = result_for(make_context(documents=()), "DOC-001-REQUIRED-DOCUMENTS")
    assert result.outcome is RuleOutcome.UNKNOWN
    assert [m.code for m in result.missing_information] == ["document.CLINICAL_NOTES"]
    assert result.missing_information[0].source is MissingInformationSource.PROVIDER
    assert result.escalation_rule_id == "ESC-001"


def test_documents_not_required_when_pre_authorisation_is_not():
    ctx = make_context(pre_auth_required=False, documents=())
    assert outcomes(ctx)["DOC-001-REQUIRED-DOCUMENTS"] is RuleOutcome.PASS


@pytest.mark.parametrize(
    ("cost", "approved", "sub_limit", "expected"),
    [
        (21_000, 0, None, RuleOutcome.PASS),
        (1_200_000, 0, None, RuleOutcome.UNKNOWN),       # above the annual limit
        (900_000, 500_000, None, RuleOutcome.UNKNOWN),   # above what is left this year
        (8_000, 0, 6_000, RuleOutcome.UNKNOWN),          # above the benefit sub-limit
        (5_000, 0, 6_000, RuleOutcome.PASS),
    ],
)
def test_tier_limits(cost, approved, sub_limit, expected):
    ctx = make_context(cost=cost, approved_this_year=approved, sub_limit=sub_limit)
    result = result_for(ctx, "LIM-001-TIER-LIMITS")
    assert result.outcome is expected
    if expected is RuleOutcome.UNKNOWN:
        assert result.escalation_rule_id == "ESC-003"
        assert "AED" in result.explanation


@pytest.mark.parametrize(
    ("tier_id", "cost", "pre_auth_required", "expected"),
    [
        ("BASIC", 500, False, False),         # below the AED 1,000 threshold
        ("BASIC", 1_500, False, True),        # at or above it
        ("EXECUTIVE", 5_000, False, False),   # below the AED 10,000 threshold
        ("EXECUTIVE", 5_000, True, True),     # schedule requires it regardless of amount
    ],
)
def test_pre_authorisation_threshold(tier_id, cost, pre_auth_required, expected):
    ctx = make_context(tier_id=tier_id, cost=cost, pre_auth_required=pre_auth_required)
    result = result_for(ctx, "AUTH-001-PRE-AUTHORISATION-REQUIRED")
    assert result.outcome is RuleOutcome.PASS
    assert result.evidence["pre_authorisation_required"] is expected


# --------------------------------------------------------------------------- invariants


def test_every_result_cites_a_source_or_explains_why_not():
    for result in ENGINE.evaluate(make_context()).results:
        assert result.sources, result.rule_id


def test_no_missing_information_ever_yields_fail():
    for ctx in [
        make_context(documents=()),
        make_context(procedure=None, coverage=None),
        make_context(cost=5_000_000),
        make_context(directory_status=DirectoryStatus.SUSPENDED),
    ]:
        for r in ENGINE.evaluate(ctx).results:
            if r.missing_information:
                assert r.outcome is RuleOutcome.UNKNOWN


def test_rule_result_invariants():
    missing = MissingInformation(code="x", description="x", source=MissingInformationSource.PROVIDER)
    with pytest.raises(ValidationError):
        RuleResult(rule_id="R", rule_version="1", outcome=RuleOutcome.UNKNOWN, explanation="", evidence={})
    with pytest.raises(ValidationError):
        RuleResult(
            rule_id="R", rule_version="1", outcome=RuleOutcome.PASS, explanation="", evidence={},
            missing_information=(missing,),
        )
    with pytest.raises(ValidationError, match="escalation"):
        RuleResult(
            rule_id="R", rule_version="1", outcome=RuleOutcome.FAIL, explanation="", evidence={},
            escalation_rule_id="ESC-001",
        )


def test_failed_result_is_immutable_and_survives_round_trip():
    result = result_for(make_context(covered=False), "COV-002-TIER-COVERS-PROCEDURE")
    with pytest.raises(ValidationError):
        result.outcome = RuleOutcome.PASS  # type: ignore[misc]
    assert RuleResult.model_validate(result.model_dump(mode="json")) == result


def test_evaluation_is_reproducible_from_stored_snapshot():
    ctx = make_context(documents=(), cost=2_000_000)
    stored = ctx.model_dump(mode="json")
    assert ENGINE.evaluate(RuleContext.model_validate(stored)) == ENGINE.evaluate(ctx)


def test_rule_exceptions_propagate():
    class Broken:
        rule_id, version, description, category = "BROKEN", "1", "broken", "COVERAGE"

        def evaluate(self, ctx):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        RuleSetEngine("t", "1", [Broken()]).evaluate(make_context())


def test_misattributed_result_is_rejected():
    class Liar:
        rule_id, version, description, category = "A", "1", "liar", "COVERAGE"

        def evaluate(self, ctx):
            return RuleResult(rule_id="B", rule_version="1", outcome=RuleOutcome.PASS, explanation="", evidence={})

    with pytest.raises(IntegrityViolationError):
        RuleSetEngine("t", "1", [Liar()]).evaluate(make_context())
