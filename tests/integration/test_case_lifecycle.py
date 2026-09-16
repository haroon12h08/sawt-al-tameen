import pytest
from pydantic import ValidationError
from sqlalchemy import update

from preauth.application.commands import CaseInformationUpdate, CloseCaseCommand, CreateCaseCommand
from preauth.domain.enums import CaseStatus, CloseReason, DocumentType, RecommendationOutcome, RuleOutcome
from preauth.domain.errors import (
    AuthorizationError,
    ConcurrencyConflictError,
    InvalidStateTransitionError,
    NotFoundError,
    OperationNotAllowedError,
    ValidationFailedError,
)
from preauth.infrastructure.db.models import PreAuthorizationCase
from tests.integration.helpers import (
    AGENT,
    PORTAL,
    REVIEWER,
    SYSTEM,
    case_in_review,
    complete_info,
    document,
    evaluated_case,
    event_types,
    new_case,
)

# --------------------------------------------------------------------------- creation


def test_create_empty_case_is_received(services):
    case = services.cases.create_case(AGENT, CreateCaseCommand())
    assert case.status is CaseStatus.RECEIVED
    assert case.case_reference.startswith("PA-") and len(case.case_reference) == 11
    assert event_types(services, case.id) == ["CASE_CREATED"]


def test_create_with_information_moves_to_collection(services):
    case = services.cases.create_case(AGENT, CreateCaseCommand(information=complete_info()))
    assert case.status is CaseStatus.INFORMATION_COLLECTION
    assert case.provider.provider_number == "PRV-100234"
    assert case.patient.member_id == "MBR-5001-01"
    assert case.requested_service.procedure_description == "MRI of knee without contrast"
    assert event_types(services, case.id) == ["CASE_CREATED", "INFORMATION_COLLECTED", "CASE_STATUS_CHANGED"]


def test_case_lookup_by_reference(services):
    case = services.cases.create_case(AGENT, CreateCaseCommand())
    assert services.queries.find_case_id_by_reference(case.case_reference, AGENT) == case.id
    with pytest.raises(NotFoundError):
        services.queries.find_case_id_by_reference("PA-00000000", AGENT)


@pytest.mark.parametrize("actor", [SYSTEM, REVIEWER])
def test_only_provider_channels_create_cases(services, actor):
    with pytest.raises(AuthorizationError):
        services.cases.create_case(actor, CreateCaseCommand())


def test_unknown_case(services):
    with pytest.raises(NotFoundError) as exc:
        services.queries.get_case("00000000-0000-0000-0000-000000000000", AGENT)
    assert exc.value.code == "CASE_NOT_FOUND"


# --------------------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"provider_number": "PRV-999999"}, "UNKNOWN_PROVIDER"),
        ({"patient": {"member_id": "MBR-5001-01", "date_of_birth": "1984-03-13"}}, "MEMBER_NOT_VERIFIED"),
        ({"patient": {"member_id": "MBR-9999-99", "date_of_birth": "1984-03-12"}}, "MEMBER_NOT_VERIFIED"),
        ({"policy_number": "POL-999999"}, "UNKNOWN_POLICY"),
        ({"policy_number": "POL-000102"}, "POLICY_MEMBER_MISMATCH"),
        ({"procedure_code": "PROC-UNKNOWN"}, "UNKNOWN_PROCEDURE"),
        ({"requested_service_date": "2026-09-01"}, "SERVICE_DATE_IN_PAST"),
    ],
)
def test_reference_validation_rejects_whole_update(services, override, code):
    case = services.cases.create_case(AGENT, CreateCaseCommand())
    with pytest.raises(ValidationFailedError) as exc:
        services.cases.update_information(case.id, AGENT, complete_info(**override))
    assert exc.value.code == code
    after = services.queries.get_case(case.id, AGENT)
    assert after.status is CaseStatus.RECEIVED and after.provider is None
    assert event_types(services, case.id) == ["CASE_CREATED"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"diagnosis_code": None},
        {"diagnosis_code": "not-icd"},
        {"conservative_treatment_weeks": -1},
        {"urgency": "WHENEVER"},
        {"unexpected_field": "x"},
        {"patient": {"member_id": "MBR-5001-01"}},
    ],
)
def test_schema_validation_is_strict(payload):
    with pytest.raises(ValidationError):
        CaseInformationUpdate(**payload)


def test_modifying_information_records_previous_values(services):
    case_id = new_case(services, docs=())
    services.cases.update_information(case_id, AGENT, CaseInformationUpdate(conservative_treatment_weeks=9))
    history = services.queries.get_audit_history(case_id, REVIEWER)
    modified = [e for e in history if e.event_type == "INFORMATION_MODIFIED"][-1]
    assert modified.data["fields"]["conservative_treatment_weeks"] == {"previous": 8, "new": 9}


def test_resubmitting_identical_information_is_a_no_op(services):
    case_id = new_case(services, docs=())
    before = event_types(services, case_id)
    services.cases.update_information(case_id, AGENT, complete_info())
    assert event_types(services, case_id) == before


# --------------------------------------------------------------------------- evaluation & state


def test_incomplete_intake_goes_to_pending_information_without_recommendation(services):
    case = services.cases.create_case(
        AGENT, CreateCaseCommand(information=CaseInformationUpdate(provider_number="PRV-100234"))
    )
    result = services.evaluation.submit_for_evaluation(case.id, AGENT)
    assert result.status is CaseStatus.PENDING_INFORMATION
    assert result.validation_passed is False and result.recommendation is None
    assert "intake.patient" in {m.code for m in result.missing_information}
    assert "VALIDATION_FAILED" in event_types(services, case.id)
    with pytest.raises(NotFoundError):
        services.queries.get_latest_recommendation(case.id, AGENT)


def test_complete_case_recommends_approval_and_awaits_routing(services):
    case_id, result = evaluated_case(services)
    assert result.status is CaseStatus.RECOMMENDATION_READY
    rec = result.recommendation
    assert rec.outcome is RecommendationOutcome.RECOMMEND_APPROVAL
    assert rec.advisory_only is True
    assert len(rec.rule_results) == 8 and all(r.outcome is RuleOutcome.PASS for r in rec.rule_results)
    assert rec.ruleset_version and rec.engine_version
    # The system never finalises: no decision exists.
    assert services.queries.get_status(case_id, AGENT).final_decision is None


def test_missing_documentation_yields_unknown_and_request_for_information(services):
    case_id, result = evaluated_case(services, docs=())
    assert result.status is CaseStatus.PENDING_INFORMATION
    assert result.recommendation.outcome is RecommendationOutcome.REQUEST_MORE_INFORMATION
    doc_rule = next(r for r in result.recommendation.rule_results if r.rule_id == "DOC-001-REQUIRED-DOCUMENTS")
    assert doc_rule.outcome is RuleOutcome.UNKNOWN
    assert [m.code for m in result.missing_information] == ["document.CLINICAL_NOTES"]


def test_providing_missing_information_allows_re_evaluation(services):
    case_id, _ = evaluated_case(services, docs=())
    services.cases.register_document(case_id, AGENT, document(DocumentType.CLINICAL_NOTES))
    assert services.queries.get_status(case_id, AGENT).status is CaseStatus.INFORMATION_COLLECTION
    result = services.evaluation.submit_for_evaluation(case_id, AGENT)
    assert result.recommendation.outcome is RecommendationOutcome.RECOMMEND_APPROVAL


def test_evaluation_requires_information_collection_state(services):
    case_id, _ = evaluated_case(services)  # RECOMMENDATION_READY
    with pytest.raises(InvalidStateTransitionError):
        services.evaluation.submit_for_evaluation(case_id, AGENT)
    empty = services.cases.create_case(AGENT, CreateCaseCommand())
    with pytest.raises(InvalidStateTransitionError):
        services.evaluation.submit_for_evaluation(empty.id, AGENT)


def test_system_actor_cannot_submit_for_evaluation(services):
    case_id = new_case(services)
    with pytest.raises(AuthorizationError):
        services.evaluation.submit_for_evaluation(case_id, SYSTEM)


def test_rule_failure_recommends_denial(services):
    info = complete_info(patient={"member_id": "MBR-5006-01", "date_of_birth": "1958-05-17"}, policy_number="POL-000106")
    _, result = evaluated_case(services, info)
    assert result.recommendation.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    policy_rule = next(r for r in result.recommendation.rule_results if r.rule_id == "ELIG-001-POLICY-ACTIVE")
    assert policy_rule.outcome is RuleOutcome.FAIL


def test_annual_limit_counts_only_human_approved_cases(services):
    from tests.integration.helpers import decide
    from preauth.domain.enums import HumanDecisionType

    info = complete_info(procedure_code="PROC-KNEE-ARTHROSCOPY", diagnosis_code="M23.221")
    docs = (DocumentType.CLINICAL_NOTES, DocumentType.IMAGING_REPORT)
    first, _ = case_in_review(services, info, docs)
    # Recommended for approval but not yet decided: does not count.
    _, pending = evaluated_case(services, info, docs)
    assert pending.recommendation.outcome is RecommendationOutcome.RECOMMEND_APPROVAL
    decide(services, first, REVIEWER, HumanDecisionType.APPROVE)
    _, after = evaluated_case(services, info, docs)
    assert after.recommendation.outcome is RecommendationOutcome.RECOMMEND_DENIAL
    assert after.recommendation.determining_rule_ids == ["LIM-001-ANNUAL-CASE-LIMIT"]


def test_updates_rejected_while_under_review(services):
    case_id, _ = case_in_review(services)
    with pytest.raises(OperationNotAllowedError) as exc:
        services.cases.update_information(case_id, AGENT, CaseInformationUpdate(conservative_treatment_weeks=12))
    assert exc.value.code == "CASE_NOT_EDITABLE"
    with pytest.raises(OperationNotAllowedError):
        services.cases.register_document(case_id, AGENT, document())


def test_changing_information_after_recommendation_requires_re_evaluation(services):
    case_id, _ = evaluated_case(services)
    services.cases.update_information(case_id, AGENT, CaseInformationUpdate(conservative_treatment_weeks=12))
    assert services.queries.get_status(case_id, AGENT).status is CaseStatus.INFORMATION_COLLECTION
    with pytest.raises(OperationNotAllowedError) as exc:
        services.review.request_human_review(case_id, AGENT)
    assert exc.value.code == "RECOMMENDATION_NOT_READY"


def test_required_information_before_and_after_intake(services):
    empty = services.cases.create_case(AGENT, CreateCaseCommand())
    view = services.queries.get_required_information(empty.id, AGENT)
    assert view.intake_complete is False and len(view.missing_information) == 8

    case_id = new_case(services, complete_info(conservative_treatment_weeks=None), docs=())
    before = event_types(services, case_id)
    view = services.queries.get_required_information(case_id, AGENT)
    assert view.intake_complete is True
    assert {m.code for m in view.missing_information} == {
        "document.CLINICAL_NOTES",
        "clinical.conservative_treatment_weeks",
    }
    assert event_types(services, case_id) == before  # dry run leaves no trace


# --------------------------------------------------------------------------- closure


def test_withdraw_and_close_terminal(services):
    case_id = new_case(services)
    closed = services.cases.close_case(case_id, PORTAL, CloseCaseCommand(reason=CloseReason.WITHDRAWN_BY_PROVIDER))
    assert closed.status is CaseStatus.CLOSED and closed.closed_at is not None
    assert event_types(services, case_id)[-2:] == ["CASE_STATUS_CHANGED", "CASE_CLOSED"]
    with pytest.raises(OperationNotAllowedError):
        services.cases.update_information(case_id, AGENT, CaseInformationUpdate(urgency="EXPEDITED"))


def test_cannot_withdraw_case_under_review(services):
    case_id, _ = case_in_review(services)
    with pytest.raises(InvalidStateTransitionError):
        services.cases.close_case(case_id, AGENT, CloseCaseCommand(reason=CloseReason.WITHDRAWN_BY_PROVIDER))


def test_close_reason_must_match_state(services):
    case_id = new_case(services)
    with pytest.raises(OperationNotAllowedError) as exc:
        services.cases.close_case(case_id, AGENT, CloseCaseCommand(reason=CloseReason.DECISION_COMMUNICATED))
    assert exc.value.code == "INVALID_CLOSE_REASON"


# --------------------------------------------------------------------------- concurrency


def test_concurrent_modification_is_detected(services, seeded_session_factory, monkeypatch):
    """Another writer commits between our read and our write: optimistic locking must reject our write."""
    from preauth.infrastructure.db.repositories import CaseRepository

    case_id = new_case(services, docs=())
    real_get = CaseRepository.get

    def get_then_concurrent_write(self, cid):
        case = real_get(self, cid)
        with seeded_session_factory() as other:
            other.execute(
                update(PreAuthorizationCase)
                .where(PreAuthorizationCase.id == cid)
                .values(version=PreAuthorizationCase.version + 1)
            )
            other.commit()
        return case

    monkeypatch.setattr(CaseRepository, "get", get_then_concurrent_write)
    with pytest.raises(ConcurrencyConflictError):
        services.cases.update_information(case_id, AGENT, CaseInformationUpdate(conservative_treatment_weeks=10))
    monkeypatch.undo()
    assert services.queries.get_case(case_id, AGENT).conservative_treatment_weeks == 8
