"""Human-review endpoints: the only path to a final authorisation outcome."""

from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, status

from preauth.api.actor import ActorDep
from preauth.api.errors import error_responses
from preauth.api.routes.cases import CaseId, _doc, _services
from preauth.application.commands import HumanDecisionCommand, ResolveCallbackCommand
from preauth.application.views import (
    CallbackView,
    CaseStatusView,
    ReviewDecisionView,
    ReviewPacketView,
    ReviewQueueItemView,
)
from preauth.domain.enums import CallbackStatus, ReviewQueue

router = APIRouter(prefix="/api/v1/review", tags=["Human review"])

REVIEWER = "Actor type `HUMAN_REVIEWER`."
REVIEWER_WITH_ROLE = (
    "Actor type `HUMAN_REVIEWER` with role `CLINICAL_REVIEWER` (case `PENDING_HUMAN_REVIEW`) or "
    "`MEDICAL_DIRECTOR` (case `ESCALATED`)."
)


@router.get(
    "/queues/{queue}",
    summary="List a review queue",
    description=_doc("Cases awaiting review in the queue; expedited first, then oldest first.", REVIEWER, "None."),
    responses=error_responses(r403="HUMAN_REVIEWER_REQUIRED"),
)
def review_queue(
    request: Request, actor: ActorDep, queue: ReviewQueue, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[ReviewQueueItemView]:
    return _services(request).review.review_queue(actor, queue, limit)


@router.get(
    "/cases/{case_id}",
    summary="Retrieve the review packet",
    description=_doc(
        "Everything a reviewer needs: case details, provider request, collected information, documents, current "
        "and historical recommendations with rule results and evidence, prior decisions, and full audit history.",
        REVIEWER,
        "None.",
    ),
    responses=error_responses(r403="HUMAN_REVIEWER_REQUIRED", r404="CASE_NOT_FOUND"),
)
def review_packet(request: Request, actor: ActorDep, case_id: CaseId) -> ReviewPacketView:
    return _services(request).review.review_packet(case_id, actor)


@router.post(
    "/cases/{case_id}/assignment",
    summary="Assign the case to yourself",
    description=_doc(
        "Self-assignment. A decision can only be recorded by the assigned reviewer. Reassignment is allowed and "
        "audited with the previous assignee.",
        REVIEWER_WITH_ROLE,
        "None (records `REVIEWER_ASSIGNED`).",
    ),
    responses=error_responses(
        r403="HUMAN_REVIEWER_REQUIRED / INSUFFICIENT_REVIEWER_ROLE",
        r404="CASE_NOT_FOUND",
        r409="CASE_NOT_UNDER_REVIEW / CONCURRENT_MODIFICATION",
    ),
)
def assign(request: Request, actor: ActorDep, case_id: CaseId) -> CaseStatusView:
    return _services(request).review.assign_reviewer(case_id, actor)


@router.post(
    "/cases/{case_id}/decision",
    status_code=status.HTTP_201_CREATED,
    summary="Record a human decision",
    description=_doc(
        "Records the reviewer's decision against the current recommendation (`recommendation_id` must be the "
        "latest; guards against deciding on stale information). A rationale is mandatory. The system "
        "recommendation is never modified; departures are flagged `is_override` and audited as "
        "`RECOMMENDATION_OVERRIDDEN`.",
        REVIEWER_WITH_ROLE + " Must be the assigned reviewer.",
        "`PENDING_HUMAN_REVIEW` → `APPROVED` / `DENIED` / `PENDING_INFORMATION` / `ESCALATED`; "
        "`ESCALATED` → `APPROVED` / `DENIED` / `PENDING_INFORMATION`.",
    ),
    responses=error_responses(
        r403="HUMAN_REVIEWER_REQUIRED / INSUFFICIENT_REVIEWER_ROLE / REVIEWER_NOT_ASSIGNED",
        r404="CASE_NOT_FOUND",
        r409="CASE_NOT_UNDER_REVIEW / STALE_RECOMMENDATION / INVALID_STATE_TRANSITION / CONCURRENT_MODIFICATION",
    ),
)
def record_decision(
    request: Request, actor: ActorDep, case_id: CaseId, body: HumanDecisionCommand
) -> ReviewDecisionView:
    return _services(request).review.record_decision(case_id, actor, body)


CallbackId = Annotated[
    str, Path(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", description="Callback UUID")
]


@router.get(
    "/callbacks",
    summary="List human callback requests",
    description=_doc(
        "Callers the voice agent handed to a human (ambiguous or non-standard requests, supplier enquiries, "
        "complaints, urgent concerns, unsupported languages). Oldest first.",
        REVIEWER,
        "None.",
    ),
    responses=error_responses(r403="HUMAN_REVIEWER_REQUIRED"),
)
def list_callbacks(
    request: Request,
    actor: ActorDep,
    status: CallbackStatus | None = CallbackStatus.OPEN,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[CallbackView]:
    return _services(request).callbacks.list_callbacks(actor, status, limit)


@router.post(
    "/callbacks/{callback_id}/resolution",
    summary="Resolve a callback request",
    description=_doc(
        "Marks a callback as handled with a note. Records `HUMAN_CALLBACK_RESOLVED` on the linked case, if any.",
        REVIEWER,
        "None (callback `OPEN` → `RESOLVED`).",
    ),
    responses=error_responses(
        r403="HUMAN_REVIEWER_REQUIRED", r404="CALLBACK_NOT_FOUND", r409="CALLBACK_ALREADY_RESOLVED"
    ),
)
def resolve_callback(
    request: Request, actor: ActorDep, callback_id: CallbackId, body: ResolveCallbackCommand
) -> CallbackView:
    return _services(request).callbacks.resolve_callback(callback_id, actor, body)
