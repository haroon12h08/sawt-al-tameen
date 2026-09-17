"""The three desk operations against the real catalogue loaded from knowledge_base/."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from preauth.application.commands import CoverageCheckCommand, LogTranscriptCommand, VerifyCallerCommand
from preauth.domain.enums import (
    CallbackStatus,
    CallOutcome,
    CaseStatus,
    DocumentType,
    RecommendationOutcome,
    ReviewQueue,
    RuleOutcome,
)
from preauth.domain.errors import AuthorizationError, NotFoundError, OperationNotAllowedError
from tests.integration.helpers import (
    AGENT,
    BARIATRIC_HOSPITAL,
    BASIC_MEMBER,
    COMPREHENSIVE_MEMBER,
    ENHANCED_MEMBER,
    EXECUTIVE_MEMBER,
    EXECUTIVE_ONLY_HOSPITAL,
    LAPSED_MEMBER,
    NEW_MEMBER,
    ORTHO_HOSPITAL,
    PLASTICS_CLINIC,
    REVIEWER,
    SUSPENDED_CLINIC,
    SYSTEM,
    add_documents,
    approved_case,
    check,
    event_types,
    log,
    verify,
)

CATALOGUE = Path(__file__).resolve().parents[2] / "knowledge_base"
PROCEDURES = json.loads((CATALOGUE / "procedure_coverage.json").read_text())["procedures"]
MEMBERS = json.loads((CATALOGUE / "sample_members.json").read_text())["members"]
AMBIGUOUS = [p for p in PROCEDURES if p["decision_class"] == "AMBIGUOUS"]
LAPSED_MEMBERS = [m for m in MEMBERS if m["policy_status"] == "lapsed"]


# --------------------------------------------------------------------------- verify_caller


def test_active_member_is_identified_with_tier_and_dependents(services):
    result = verify(services)
    assert result.authorised is True and result.failure_code is None
    assert result.member.tier.tier_id == "EXECUTIVE"
    assert result.member.policy_number == EXECUTIVE_MEMBER[0]
    assert [d.relationship for d in result.member.dependents] == ["spouse", "child"]
    assert result.provider.name == "Al Hudaiba Crescent Hospital"


def test_every_lapsed_member_in_the_catalogue_is_rejected(services):
    assert len(LAPSED_MEMBERS) == 3
    for member in LAPSED_MEMBERS:
        result = verify(
            services, member=(member["policy_number"], date.fromisoformat(member["date_of_birth"]))
        )
        assert result.authorised is False, member["member_id"]
        assert result.failure_code == "POLICY_NOT_ACTIVE"
        assert "lapsed" in result.failure_reason


def test_every_active_member_in_the_catalogue_is_accepted(services):
    active = [m for m in MEMBERS if m["policy_status"] == "active"]
    assert len(active) == 17
    for member in active:
        result = verify(
            services, member=(member["policy_number"], date.fromisoformat(member["date_of_birth"]))
        )
        assert result.authorised is True, member["member_id"]
        assert result.member.tier.tier_id == member["tier"]


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ((EXECUTIVE_MEMBER[0], date(1990, 1, 1)), "MEMBER_NOT_VERIFIED"),
        (("POL-SA-2026-999999", date(1986, 4, 17)), "MEMBER_NOT_VERIFIED"),
    ],
)
def test_member_verification_failures_are_indistinguishable(services, member, expected):
    result = verify(services, member=member)
    assert result.authorised is False and result.failure_code == expected


def test_unknown_and_inactive_providers_are_rejected(services):
    assert verify(services, provider="PRV-99999").failure_code == "UNKNOWN_PROVIDER"
    result = verify(services, provider=SUSPENDED_CLINIC)
    assert result.failure_code == "PROVIDER_NOT_ACTIVE" and "suspended" in result.failure_reason


def test_supplier_verification_returns_outstanding_onboarding_documents(services):
    result = verify(
        services, provider="ONB-APP-2026-0007", caller_role="SUPPLIER", organisation="Gulf Medical Supplies",
        member=None,
    )
    assert result.authorised is True and result.member is None
    outstanding = {o["requirement_id"]: o["status"] for o in result.onboarding.outstanding_requirements}
    assert outstanding == {"ONB-003": "expired", "ONB-006": "missing"}
    assert result.onboarding.outstanding_requirements[0]["name"]


def test_unknown_onboarding_reference_is_rejected(services):
    result = verify(
        services, provider="ONB-APP-9999", caller_role="SUPPLIER", organisation="Unknown Co", member=None
    )
    assert result.authorised is False and result.failure_code == "UNKNOWN_ONBOARDING_REFERENCE"


# --------------------------------------------------------------------------- check_coverage_rule guards


def test_coverage_check_requires_a_successful_verification(services):
    unverified = verify(services, member=LAPSED_MEMBER)
    with pytest.raises(AuthorizationError) as exc:
        check(services, unverified)
    assert exc.value.code == "CALLER_NOT_VERIFIED"


def test_coverage_check_requires_a_verified_member(services):
    verification = verify(services, member=None)
    with pytest.raises(OperationNotAllowedError) as exc:
        check(services, verification)
    assert exc.value.code == "MEMBER_NOT_VERIFIED"


def test_suppliers_cannot_request_a_coverage_check(services):
    verification = verify(
        services, provider="ONB-APP-2026-0007", caller_role="SUPPLIER", organisation="Gulf Medical Supplies",
        member=None,
    )
    with pytest.raises(AuthorizationError) as exc:
        check(services, verification)
    assert exc.value.code in ("CALLER_ROLE_NOT_ELIGIBLE", "MEMBER_NOT_VERIFIED")


def test_unknown_verification_id_is_rejected(services):
    with pytest.raises(NotFoundError):
        services.desk.check_coverage_rule(
            AGENT,
            CoverageCheckCommand(
                verification_id="00000000-0000-4000-8000-000000000000",
                procedure_code="SP-20040",
                treatment_date=(date.today() + timedelta(days=10)).isoformat(),
                estimated_cost_aed=1000,
            ),
        )


# --------------------------------------------------------------------------- check_coverage_rule outcomes


def test_complete_request_recommends_approval_and_routes_for_sign_off(services):
    _, result = approved_case(services)
    assert result.outcome is RecommendationOutcome.RECOMMEND_APPROVAL
    assert result.status is CaseStatus.PENDING_HUMAN_REVIEW
    assert result.review_queue is ReviewQueue.CLINICAL_REVIEW
    assert result.advisory_only is True
    assert result.pre_authorisation_required is True
    assert "sign-off" in result.headline
    assert any("Schedule of Benefits" in s.document for s in result.sources)
    # The system has not decided anything.
    assert services.queries.get_status(result.case_id, AGENT).final_decision is None


def test_missing_documents_request_more_information(services):
    verification = verify(services)
    result = check(services, verification, procedure_code="SP-20040", cost=21000)
    assert result.outcome is RecommendationOutcome.REQUEST_MORE_INFORMATION
    assert result.status is CaseStatus.PENDING_INFORMATION
    assert {m.code for m in result.missing_information} == {
        "document.CLINICAL_NOTES", "document.OPERATIVE_PLAN", "document.PRIOR_TREATMENT_RECORD"
    }
    assert "portal" in result.next_step


def test_documents_then_recheck_reaches_approval(services):
    verification = verify(services)
    first = check(services, verification, procedure_code="SP-10040", cost=2600)
    assert first.outcome is RecommendationOutcome.REQUEST_MORE_INFORMATION
    add_documents(services, first.case_id, [DocumentType.CLINICAL_NOTES])
    second = check(
        services, verification, procedure_code="SP-10040", cost=2600, case_reference=first.case_reference
    )
    assert second.case_reference == first.case_reference
    assert second.outcome is RecommendationOutcome.RECOMMEND_APPROVAL


def test_tier_boundary_recommends_denial(services):
    verification = verify(services, member=BASIC_MEMBER, organisation="Al Hudaiba Crescent Hospital")
    result = check(services, verification, procedure_code="SP-20050", cost=62000)
    assert result.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    assert "Enhanced" in result.rationale


def test_excluded_procedure_recommends_denial(services):
    verification = verify(
        services, provider=PLASTICS_CLINIC, member=ENHANCED_MEMBER, organisation="Jumeirah Dunes Specialist Centre"
    )
    result = check(services, verification, procedure_code="SP-20140", cost=32000)
    assert result.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    assert "Excluded on all tiers" in result.rationale


def test_out_of_network_provider_recommends_denial(services):
    verification = verify(
        services, provider=EXECUTIVE_ONLY_HOSPITAL, member=BASIC_MEMBER, organisation="Gulf Meridian"
    )
    result = check(services, verification, procedure_code="SP-20010", cost=18500)
    assert result.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    assert "outside the Basic Network" in result.rationale


def test_amount_over_the_tier_limit_escalates_with_esc_003(services):
    verification = verify(services, member=BASIC_MEMBER, organisation="Al Hudaiba Crescent Hospital")
    result = check(services, verification, procedure_code="SP-20040", cost=200_000)
    assert result.outcome is RecommendationOutcome.ESCALATE
    assert "ESC-003" in [c.rule_id for c in result.escalation_citations]


def test_waiting_period_escalates_with_esc_007(services):
    verification = verify(services, member=NEW_MEMBER, organisation="Al Hudaiba Crescent Hospital")
    result = check(services, verification, procedure_code="SP-30020", cost=12000)
    assert result.outcome is RecommendationOutcome.ESCALATE
    assert "ESC-007" in [c.rule_id for c in result.escalation_citations]


def test_procedure_not_in_the_schedule_escalates_with_esc_005(services):
    verification = verify(services)
    result = check(services, verification, procedure_code="SP-99999", cost=5000)
    assert result.outcome is RecommendationOutcome.ESCALATE
    assert "ESC-005" in [c.rule_id for c in result.escalation_citations]


@pytest.mark.parametrize("procedure", AMBIGUOUS, ids=[p["code"] for p in AMBIGUOUS])
def test_every_ambiguous_procedure_escalates_with_its_own_rule(services, procedure):
    """All 11 ambiguous procedures escalate and cite the ESC-### rule the catalogue assigns them."""
    expected = procedure["escalation"]["escalation_rule_id"]
    # Use a member and provider that clear eligibility and network, so the ambiguity is what decides.
    verification = verify(
        services, provider=BARIATRIC_HOSPITAL, member=EXECUTIVE_MEMBER, organisation="Yas Horizon"
    )
    result = check(
        services, verification, procedure_code=procedure["code"], cost=procedure["typical_billed_amount_aed"]
    )
    assert result.outcome is RecommendationOutcome.ESCALATE, result.rationale
    cited = [c.rule_id for c in result.escalation_citations]
    assert expected in cited, f"{procedure['code']} cited {cited}, expected {expected}"
    citation = next(c for c in result.escalation_citations if c.rule_id == expected)
    assert citation.situation and citation.agent_action  # not a generic "needs review"
    assert result.status is CaseStatus.ESCALATED
    assert result.review_queue is ReviewQueue.MEDICAL_DIRECTOR_REVIEW


def test_ambiguous_case_records_the_rule_on_the_evaluation(services):
    verification = verify(
        services, provider=BARIATRIC_HOSPITAL, member=COMPREHENSIVE_MEMBER, organisation="Yas Horizon"
    )
    result = check(services, verification, procedure_code="SP-20110", cost=48000)
    recommendation = services.queries.get_latest_recommendation(result.case_id, REVIEWER)
    decisive = [r for r in recommendation.rule_results if r.rule_id == "COV-003-SCHEDULE-DECIDABLE"]
    assert decisive[0].outcome is RuleOutcome.UNKNOWN
    assert decisive[0].escalation_rule_id == "ESC-003"
    assert decisive[0].sources


def test_no_pre_authorisation_needed_below_the_threshold(services):
    verification = verify(services, member=EXECUTIVE_MEMBER)
    result = check(services, verification, procedure_code="SP-10010", cost=180)
    assert result.pre_authorisation_required is False
    assert "not required" in result.rationale


# --------------------------------------------------------------------------- log_transcript


def test_log_transcript_records_the_call_and_audits_the_case(services):
    verification, result = approved_case(services)
    entry = log(
        services,
        outcome=CallOutcome.RECOMMENDATION_PREPARED,
        case_reference=result.case_reference,
        verification=verification,
        summary="Knee arthroscopy requested; recommendation prepared for sign-off.",
    )
    assert entry.reference.startswith("CL-")
    assert entry.outcome_communicated is CallOutcome.RECOMMENDATION_PREPARED
    assert "CALL_SUMMARY_LOGGED" in event_types(services, result.case_id)


def test_escalation_log_raises_a_callback(services):
    verification = verify(
        services, provider=BARIATRIC_HOSPITAL, member=COMPREHENSIVE_MEMBER, organisation="Yas Horizon"
    )
    result = check(services, verification, procedure_code="SP-20110", cost=48000)
    entry = log(
        services,
        outcome=CallOutcome.ESCALATED,
        case_reference=result.case_reference,
        verification=verification,
        summary="Sleeve gastrectomy referred to the medical director for eligibility criteria.",
        callback_phone="+971501234567",
        caller_name="Noura Al Ameri",
    )
    assert entry.callback_id is not None
    callbacks = services.callbacks.list_callbacks(REVIEWER, CallbackStatus.OPEN)
    assert [c.id for c in callbacks] == [entry.callback_id]
    assert callbacks[0].case_id == result.case_id


def test_log_without_a_case_is_still_recorded(services):
    entry = log(
        services,
        outcome=CallOutcome.OUT_OF_SCOPE,
        summary="Patient called the provider line; redirected to member services.",
        callback_phone="+971509998877",
        caller_name="Walk-in caller",
    )
    assert entry.case_id is None and entry.callback_id is not None

