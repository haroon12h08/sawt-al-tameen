"""Provider-interaction endpoints. Thin adapters: parse, call one application service, return its view."""

from typing import Annotated

from fastapi import APIRouter, Path, Request, status

from preauth.api.actor import ActorDep
from preauth.api.errors import error_responses
from preauth.application.commands import (
    CaseInformationUpdate,
    CloseCaseCommand,
    CreateCaseCommand,
    RegisterDocumentCommand,
)
from preauth.application.services import ApplicationServices
from preauth.application.views import (
    AuditEventView,
    CaseStatusView,
    CaseView,
    DocumentView,
    EvaluationResultView,
    RecommendationView,
    RequiredInformationView,
)

router = APIRouter(prefix="/api/v1/cases")

CaseId = Annotated[
    str,
    Path(
        description="Case UUID",
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    ),
]

PROVIDER_CHANNELS = "Actor type `PROVIDER_PORTAL` or `VOICE_AGENT`."
ANY_ACTOR = "Any authenticated actor."


def _doc(purpose: str, auth: str, transitions: str) -> str:
    return f"{purpose}\n\n**Authorisation:** {auth}\n\n**State transitions:** {transitions}"


def _services(request: Request) -> ApplicationServices:
    return request.app.state.services


@router.post(
    "",
    tags=["Provider interaction"],
    status_code=status.HTTP_201_CREATED,
    summary="Create a pre-authorisation case",
    description=_doc(
        "Opens a new case, optionally with initial information. Returns the case with a short, speakable "
        "`case_reference` suitable for quoting to a caller.",
        PROVIDER_CHANNELS,
        "→ `RECEIVED`; if information is supplied, `RECEIVED` → `INFORMATION_COLLECTION`.",
    ),
    responses=error_responses(
        r403="INTAKE_ACTOR_REQUIRED",
        r422="UNKNOWN_PROVIDER / MEMBER_NOT_VERIFIED / UNKNOWN_POLICY / POLICY_MEMBER_MISMATCH / "
        "UNKNOWN_PROCEDURE / SERVICE_DATE_IN_PAST",
    ),
)
def create_case(request: Request, actor: ActorDep, body: CreateCaseCommand) -> CaseView:
    return _services(request).cases.create_case(actor, body)


@router.get(
    "/by-reference/{case_reference}",
    tags=["Provider interaction"],
    summary="Look up a case by its reference",
    description=_doc("Resolves a caller-quoted case reference (e.g. `PA-7K3M9Q2B`).", ANY_ACTOR, "None."),
    responses=error_responses(r404="CASE_NOT_FOUND"),
)
def get_case_by_reference(
    request: Request,
    actor: ActorDep,
    case_reference: Annotated[str, Path(pattern=r"^PA-[0-9A-Z]{8}$")],
) -> CaseView:
    services = _services(request)
    return services.queries.get_case(services.queries.find_case_id_by_reference(case_reference, actor), actor)


@router.get(
    "/{case_id}",
    tags=["Provider interaction"],
    summary="Retrieve a case",
    description=_doc("Returns all collected information, documents, and current status.", ANY_ACTOR, "None."),
    responses=error_responses(r404="CASE_NOT_FOUND"),
)
def get_case(request: Request, actor: ActorDep, case_id: CaseId) -> CaseView:
    return _services(request).queries.get_case(case_id, actor)


@router.patch(
    "/{case_id}/information",
    tags=["Provider interaction"],
    summary="Submit or update collected information",
    description=_doc(
        "Partial update: only fields present in the body are applied; explicit nulls are rejected. Identifiers are "
        "verified against reference data and any failure rejects the entire update. Records "
        "`INFORMATION_COLLECTED` and/or `INFORMATION_MODIFIED` audit events with previous values.",
        PROVIDER_CHANNELS,
        "`RECEIVED` / `PENDING_INFORMATION` / `RECOMMENDATION_READY` → `INFORMATION_COLLECTION` when anything "
        "changes (a changed case must be re-evaluated). Rejected in any other non-editable state.",
    ),
    responses=error_responses(
        r403="INTAKE_ACTOR_REQUIRED",
        r404="CASE_NOT_FOUND",
        r409="CASE_NOT_EDITABLE / CONCURRENT_MODIFICATION",
        r422="UNKNOWN_PROVIDER / MEMBER_NOT_VERIFIED / UNKNOWN_POLICY / POLICY_MEMBER_MISMATCH / "
        "UNKNOWN_PROCEDURE / SERVICE_DATE_IN_PAST",
    ),
)
def update_information(
    request: Request, actor: ActorDep, case_id: CaseId, body: CaseInformationUpdate
) -> CaseView:
    return _services(request).cases.update_information(case_id, actor, body)


@router.post(
    "/{case_id}/documents",
    tags=["Provider interaction"],
    status_code=status.HTTP_201_CREATED,
    summary="Register a supporting document",
    description=_doc(
        "Registers metadata for a document already placed in the document store (`storage_uri`). File upload "
        "itself is handled by the document store, not this service.",
        PROVIDER_CHANNELS,
        "Same as information update: editable states → `INFORMATION_COLLECTION`.",
    ),
    responses=error_responses(
        r403="INTAKE_ACTOR_REQUIRED", r404="CASE_NOT_FOUND", r409="CASE_NOT_EDITABLE / CONCURRENT_MODIFICATION"
    ),
)
def register_document(
    request: Request, actor: ActorDep, case_id: CaseId, body: RegisterDocumentCommand
) -> DocumentView:
    return _services(request).cases.register_document(case_id, actor, body)


@router.get(
    "/{case_id}/required-information",
    tags=["Provider interaction"],
    summary="Retrieve required and missing information",
    description=_doc(
        "Lists intake requirements and what is still missing. When intake is complete the ruleset is run as a "
        "side-effect-free dry run to surface rule-level needs (e.g. document types). `source` distinguishes "
        "information the provider can supply from gaps the insurer must resolve.",
        ANY_ACTOR,
        "None. No audit event.",
    ),
    responses=error_responses(r404="CASE_NOT_FOUND"),
)
def required_information(request: Request, actor: ActorDep, case_id: CaseId) -> RequiredInformationView:
    return _services(request).queries.get_required_information(case_id, actor)


@router.get(
    "/{case_id}/status",
    tags=["Provider interaction"],
    summary="Retrieve case status",
    description=_doc(
        "Current status, review queue, statuses reachable from here, and the final human decision if one exists.",
        ANY_ACTOR,
        "None.",
    ),
    responses=error_responses(r404="CASE_NOT_FOUND"),
)
def case_status(request: Request, actor: ActorDep, case_id: CaseId) -> CaseStatusView:
    return _services(request).queries.get_status(case_id, actor)


@router.post(
    "/{case_id}/evaluation",
    tags=["Provider interaction"],
    summary="Submit the case for evaluation",
    description=_doc(
        "Validates intake completeness, evaluates the ruleset, and generates an advisory recommendation, "
        "atomically. Incomplete intake is an expected outcome (HTTP 200, `validation_passed=false`), not an error.",
        PROVIDER_CHANNELS,
        "`INFORMATION_COLLECTION` → `VALIDATION` → either `PENDING_INFORMATION` (intake incomplete) or "
        "`RULE_EVALUATION` → `PENDING_INFORMATION` (recommendation `REQUEST_MORE_INFORMATION`) / "
        "`RECOMMENDATION_READY` (any other recommendation). Never reaches `APPROVED` or `DENIED`.",
    ),
    responses=error_responses(
        r403="INTAKE_ACTOR_REQUIRED",
        r404="CASE_NOT_FOUND",
        r409="INVALID_STATE_TRANSITION / CONCURRENT_MODIFICATION",
    ),
)
def submit_for_evaluation(request: Request, actor: ActorDep, case_id: CaseId) -> EvaluationResultView:
    return _services(request).evaluation.submit_for_evaluation(case_id, actor)


@router.get(
    "/{case_id}/recommendation",
    tags=["Provider interaction"],
    summary="Retrieve the latest recommendation",
    description=_doc(
        "The most recent system recommendation with its rule results, evidence, rationale, and engine versions. "
        "`advisory_only` is always true: a recommendation is not an authorisation decision.",
        ANY_ACTOR,
        "None.",
    ),
    responses=error_responses(r404="CASE_NOT_FOUND / RECOMMENDATION_NOT_FOUND"),
)
def latest_recommendation(request: Request, actor: ActorDep, case_id: CaseId) -> RecommendationView:
    return _services(request).queries.get_latest_recommendation(case_id, actor)


@router.post(
    "/{case_id}/human-review-request",
    tags=["Provider interaction"],
    summary="Submit the case for human review",
    description=_doc(
        "Routes the current recommendation to a human review queue. `ESCALATE` recommendations go to the medical "
        "director queue; all others to clinical review.",
        "Actor type `PROVIDER_PORTAL`, `VOICE_AGENT`, or `SYSTEM`.",
        "`RECOMMENDATION_READY` → `PENDING_HUMAN_REVIEW` or `ESCALATED`.",
    ),
    responses=error_responses(
        r403="TRANSITION_NOT_AUTHORIZED",
        r404="CASE_NOT_FOUND",
        r409="RECOMMENDATION_NOT_READY / CONCURRENT_MODIFICATION",
    ),
)
def request_human_review(request: Request, actor: ActorDep, case_id: CaseId) -> CaseStatusView:
    return _services(request).review.request_human_review(case_id, actor)


@router.post(
    "/{case_id}/closure",
    tags=["Provider interaction"],
    summary="Close a case",
    description=_doc(
        "`WITHDRAWN_BY_PROVIDER` before review; `DECISION_COMMUNICATED` after a human decision.",
        "Withdrawal: `PROVIDER_PORTAL` or `VOICE_AGENT`. After a decision: `SYSTEM` or `HUMAN_REVIEWER`.",
        "`RECEIVED` / `INFORMATION_COLLECTION` / `PENDING_INFORMATION` / `RECOMMENDATION_READY` / `APPROVED` / "
        "`DENIED` → `CLOSED` (terminal).",
    ),
    responses=error_responses(
        r403="TRANSITION_NOT_AUTHORIZED",
        r404="CASE_NOT_FOUND",
        r409="INVALID_CLOSE_REASON / INVALID_STATE_TRANSITION / CONCURRENT_MODIFICATION",
    ),
)
def close_case(request: Request, actor: ActorDep, case_id: CaseId, body: CloseCaseCommand) -> CaseView:
    return _services(request).cases.close_case(case_id, actor, body)


@router.get(
    "/{case_id}/audit-events",
    tags=["Audit"],
    summary="Retrieve the audit trail",
    description=_doc(
        "Immutable, ordered audit events for the case. Events are append-only at the database level.",
        "Actor type `HUMAN_REVIEWER` or `SYSTEM`.",
        "None.",
    ),
    responses=error_responses(r403="HUMAN_REVIEWER_REQUIRED", r404="CASE_NOT_FOUND"),
)
def audit_events(request: Request, actor: ActorDep, case_id: CaseId) -> list[AuditEventView]:
    return _services(request).queries.get_audit_history(case_id, actor)
