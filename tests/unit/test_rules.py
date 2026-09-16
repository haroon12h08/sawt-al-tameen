from datetime import date

import pytest
from pydantic import ValidationError

from preauth.domain.enums import (
    CredentialingStatus,
    DocumentType,
    MissingInformationSource,
    NetworkStatus,
    PolicyStatus,
    RuleOutcome,
)
from preauth.domain.errors import IntegrityViolationError
from preauth.rules.engine import RuleSetEngine
from preauth.rules.mock_ruleset import build_mock_rules_engine
from preauth.rules.model import MissingInformation, RuleContext, RuleResult
from tests.unit.context_factory import make_context

ENGINE = build_mock_rules_engine()


def outcomes(ctx) -> dict[str, RuleOutcome]:
    return {r.rule_id: r.outcome for r in ENGINE.evaluate(ctx).results}


def result_for(ctx, rule_id) -> RuleResult:
    return next(r for r in ENGINE.evaluate(ctx).results if r.rule_id == rule_id)


def test_complete_request_passes_every_rule():
    assert set(outcomes(make_context()).values()) == {RuleOutcome.PASS}


def test_engine_reports_every_rule_with_version():
    evaluation = ENGINE.evaluate(make_context())
    assert evaluation.engine_name == "mock-preauth-ruleset"
    assert len(evaluation.results) == len(ENGINE.rules) == 8
    assert all(r.rule_version for r in evaluation.results)


@pytest.mark.parametrize(
    ("kwargs", "rule_id"),
    [
        ({"policy_status": PolicyStatus.LAPSED}, "ELIG-001-POLICY-ACTIVE"),
        ({"effective_to": date(2026, 6, 30)}, "ELIG-001-POLICY-ACTIVE"),
        ({"credentialing": CredentialingStatus.SUSPENDED}, "ELIG-002-PROVIDER-CREDENTIALED"),
        ({"network": NetworkStatus.OUT_OF_NETWORK}, "ELIG-003-PROVIDER-NETWORK"),
        ({"covered": False}, "COV-001-PROCEDURE-COVERED"),
        ({"diagnosis": "J34.2"}, "MED-001-DIAGNOSIS-INDICATED"),
        ({"conservative_weeks": 4}, "MED-002-CONSERVATIVE-TREATMENT"),
        ({"prior_approved": 2}, "LIM-001-ANNUAL-CASE-LIMIT"),
    ],
)
def test_rule_failures(kwargs, rule_id):
    result = outcomes(make_context(**kwargs))
    assert result[rule_id] is RuleOutcome.FAIL
    assert all(o is RuleOutcome.PASS for rid, o in result.items() if rid != rule_id)


def test_out_of_network_passes_when_plan_covers_it():
    ctx = make_context(network=NetworkStatus.OUT_OF_NETWORK, oon_covered=True)
    assert outcomes(ctx)["ELIG-003-PROVIDER-NETWORK"] is RuleOutcome.PASS


def test_missing_document_is_unknown_not_fail():
    result = result_for(make_context(documents=(DocumentType.CLINICAL_NOTES,)), "DOC-001-REQUIRED-DOCUMENTS")
    assert result.outcome is RuleOutcome.UNKNOWN
    assert [m.code for m in result.missing_information] == ["document.IMAGING_REPORT"]
    assert result.missing_information[0].source is MissingInformationSource.PROVIDER


def test_missing_conservative_treatment_is_unknown_not_fail():
    result = result_for(make_context(conservative_weeks=None), "MED-002-CONSERVATIVE-TREATMENT")
    assert result.outcome is RuleOutcome.UNKNOWN
    assert result.missing_information[0].code == "clinical.conservative_treatment_weeks"


def test_missing_coverage_terms_is_unknown_and_insurer_sourced():
    results = ENGINE.evaluate(make_context(coverage=None)).results
    coverage_dependent = [r for r in results if not r.rule_id.startswith("ELIG")]
    assert coverage_dependent
    for r in coverage_dependent:
        assert r.outcome is RuleOutcome.UNKNOWN
        assert r.missing_information[0].source is MissingInformationSource.INSURER


def test_no_missing_information_ever_yields_fail():
    """Across all incomplete-information variants, nothing that is missing is reported as FAIL."""
    for ctx in [
        make_context(documents=()),
        make_context(conservative_weeks=None),
        make_context(coverage=None),
        make_context(documents=(), conservative_weeks=None),
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
    with pytest.raises(ValidationError):
        RuleResult(
            rule_id="R", rule_version="1", outcome=RuleOutcome.FAIL, explanation="", evidence={},
            missing_information=(missing,),
        )


def test_failed_result_outcome_is_immutable():
    result = result_for(make_context(policy_status=PolicyStatus.LAPSED), "ELIG-001-POLICY-ACTIVE")
    with pytest.raises(ValidationError):
        result.outcome = RuleOutcome.PASS  # type: ignore[misc]
    assert result.outcome is RuleOutcome.FAIL


def test_failed_rule_survives_serialisation_round_trip():
    result = result_for(make_context(policy_status=PolicyStatus.LAPSED), "ELIG-001-POLICY-ACTIVE")
    restored = RuleResult.model_validate(result.model_dump(mode="json"))
    assert restored.outcome is RuleOutcome.FAIL
    assert restored == result


def test_evaluation_is_reproducible_from_stored_snapshot():
    ctx = make_context(documents=(DocumentType.CLINICAL_NOTES,), conservative_weeks=3)
    stored = ctx.model_dump(mode="json")
    replayed = ENGINE.evaluate(RuleContext.model_validate(stored))
    assert replayed == ENGINE.evaluate(ctx)


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


def test_duplicate_rule_ids_rejected():
    rule = build_mock_rules_engine().rules[0]
    with pytest.raises(ValueError):
        RuleSetEngine("t", "1", [rule, rule])
