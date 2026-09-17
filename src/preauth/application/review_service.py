"""Human review: routing to reviewers, assignment, and recording decisions.

Final authority (APPROVED / DENIED) is exercised only here, only by an assigned HUMAN_REVIEWER holding the role
required by the case's review queue, and only against the recommendation the reviewer actually saw.
"""

import logging

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.commands import HumanDecisionCommand
from preauth.application.unit_of_work import UnitOfWork
from preauth.application.voice_channel_service import pending_conversation_ids
from preauth.application.views import (
    AuditEventView,
    CallbackView,
    CallRecordView,
    CaseStatusView,
    ReviewDecisionView,
    ReviewPacketView,
    ReviewQueueItemView,
    case_view,
    recommendation_view,
    status_view,
)
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, AuditEventType, CaseStatus, HumanDecisionType, ReviewQueue
from preauth.domain.errors import AuthorizationError, OperationNotAllowedError
from preauth.domain.review import (
    DECISION_TARGET_STATUS,
    QUEUE_FOR_STATUS,
    ROLE_FOR_QUEUE,
    is_override,
    routing_status_for,
)
from preauth.infrastructure.clock import Clock, new_id
from preauth.infrastructure.db.models import PreAuthorizationCase, ReviewDecision
from preauth.infrastructure.observability import bind_case_id

logger = logging.getLogger("preauth.review")

STATUS_FOR_QUEUE = {queue: status for status, queue in QUEUE_FOR_STATUS.items()}


def require_human_reviewer(actor: Actor) -> None:
    if actor.type is not ActorType.HUMAN_REVIEWER:
        raise AuthorizationError(
            "This operation requires an authenticated human reviewer",
            code="HUMAN_REVIEWER_REQUIRED",
            details={"actor_type": actor.type},
        )


class ReviewService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock):
        self._session_factory = session_factory
        self._clock = clock

    def _uow(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory, self._clock)

    def request_human_review(self, case_id: str, actor: Actor) -> CaseStatusView:
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            if case.status is not CaseStatus.RECOMMENDATION_READY:
                raise OperationNotAllowedError(
                    "A case can be submitted for human review only when a current recommendation is ready",
                    code="RECOMMENDATION_NOT_READY",
                    details={"status": case.status},
                )
            recommendation = uow.evaluations.latest_recommendation(case.id)
            target = routing_status_for(recommendation.outcome)
            queue = QUEUE_FOR_STATUS[target]
            uow.transition(case, target, actor, reason="human_review_requested")
            case.review_requested_at = self._clock.now()
            case.assigned_reviewer_id = None
            case.assigned_at = None
            uow.audit.record(
                case.id,
                AuditEventType.HUMAN_REVIEW_REQUESTED,
                actor,
                {"recommendation_id": recommendation.id, "recommendation": recommendation.outcome, "queue": queue},
            )
            if target is CaseStatus.ESCALATED:
                uow.audit.record(
                    case.id,
                    AuditEventType.CASE_ESCALATED,
                    actor,
                    {"source": "SYSTEM_RECOMMENDATION", "recommendation_id": recommendation.id, "queue": queue},
                )
            uow.commit()
            return status_view(case, None)

    def assign_reviewer(self, case_id: str, actor: Actor) -> CaseStatusView:
        """Self-assignment by a reviewer holding the role the case's queue requires."""
        require_human_reviewer(actor)
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            queue = self._require_reviewable(case, actor)
            previous = case.assigned_reviewer_id
            case.assigned_reviewer_id = actor.id
            case.assigned_at = self._clock.now()
            case.updated_at = case.assigned_at
            uow.audit.record(
                case.id,
                AuditEventType.REVIEWER_ASSIGNED,
                actor,
                {"reviewer_id": actor.id, "previous_reviewer_id": previous, "queue": queue},
            )
            uow.commit()
            return status_view(case, None)

    def record_decision(self, case_id: str, actor: Actor, command: HumanDecisionCommand) -> ReviewDecisionView:
        require_human_reviewer(actor)
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            queue = self._require_reviewable(case, actor)
            if case.assigned_reviewer_id != actor.id:
                raise AuthorizationError(
                    "The case must be assigned to the reviewer recording the decision",
                    code="REVIEWER_NOT_ASSIGNED",
                    details={"assigned_reviewer_id": case.assigned_reviewer_id},
                )
            pending_calls = pending_conversation_ids(uow, case.id)
            if pending_calls:
                raise OperationNotAllowedError(
                    "Voice calls on this case have not been logged yet; the transcript must be on record before "
                    "any sign-off action",
                    code="CALL_RECORD_PENDING",
                    details={"conversation_ids": pending_calls},
                )
            recommendation = uow.evaluations.latest_recommendation(case.id)
            if recommendation.id != command.recommendation_id:
                raise OperationNotAllowedError(
                    "The decision must reference the current recommendation",
                    code="STALE_RECOMMENDATION",
                    details={
                        "current_recommendation_id": recommendation.id,
                        "submitted_recommendation_id": command.recommendation_id,
                    },
                )

            from_status = case.status
            target = DECISION_TARGET_STATUS[command.decision]
            override = is_override(recommendation.outcome, command.decision)
            role = ROLE_FOR_QUEUE[queue]

            uow.transition(case, target, actor, reason=f"human_decision_{command.decision.value.lower()}")
            decision = ReviewDecision(
                id=new_id(),
                case_id=case.id,
                sequence=uow.reviews.next_decision_sequence(case.id),
                recommendation_id=recommendation.id,
                decision=command.decision,
                rationale=command.rationale,
                reviewer_id=actor.id,
                reviewer_role=role,
                is_override=override,
                from_status=from_status,
                to_status=target,
                decided_at=self._clock.now(),
            )
            uow.reviews.add_decision(decision)
            uow.flush()
            uow.audit.record(
                case.id,
                AuditEventType.HUMAN_DECISION_RECORDED,
                actor,
                {
                    "decision_id": decision.id,
                    "decision": command.decision,
                    "rationale": command.rationale,
                    "reviewer_role": role,
                    "recommendation_id": recommendation.id,
                    "recommendation": recommendation.outcome,
                    "is_override": override,
                },
            )
            if override:
                uow.audit.record(
                    case.id,
                    AuditEventType.RECOMMENDATION_OVERRIDDEN,
                    actor,
                    {
                        "decision_id": decision.id,
                        "recommendation_id": recommendation.id,
                        "recommendation": recommendation.outcome,
                        "decision": command.decision,
                    },
                )
            if command.decision is HumanDecisionType.ESCALATE:
                uow.audit.record(
                    case.id,
                    AuditEventType.CASE_ESCALATED,
                    actor,
                    {"source": "HUMAN_REVIEWER", "decision_id": decision.id,
                     "queue": ReviewQueue.MEDICAL_DIRECTOR_REVIEW},
                )
            if target in (CaseStatus.ESCALATED, CaseStatus.PENDING_INFORMATION):
                # A new queue (or leaving review) requires a fresh assignment.
                case.assigned_reviewer_id = None
                case.assigned_at = None
            if target is CaseStatus.ESCALATED:
                case.review_requested_at = self._clock.now()
            uow.commit()
            logger.info(
                "human_decision_recorded",
                extra={"decision": command.decision.value, "is_override": override, "reviewer_role": role.value},
            )
            return ReviewDecisionView.model_validate(decision)

    def review_packet(self, case_id: str, actor: Actor) -> ReviewPacketView:
        require_human_reviewer(actor)
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            recommendations = [recommendation_view(r) for r in uow.evaluations.recommendations(case.id)]
            conversations = uow.voice.conversation_ids_for_case(case.id)
            return ReviewPacketView(
                case=case_view(case),
                current_recommendation=recommendations[-1] if recommendations else None,
                recommendation_history=recommendations,
                decisions=[ReviewDecisionView.model_validate(d) for d in uow.reviews.decisions(case.id)],
                calls=[CallRecordView.model_validate(r) for r in uow.voice.call_records(conversations)],
                pending_call_conversation_ids=pending_conversation_ids(uow, case.id),
                callbacks=[CallbackView.model_validate(c) for c in uow.voice.callbacks_for_case(case.id)],
                audit_history=[AuditEventView.model_validate(e) for e in uow.audit_events.for_case(case.id)],
            )

    def review_queue(self, actor: Actor, queue: ReviewQueue, limit: int = 50) -> list[ReviewQueueItemView]:
        require_human_reviewer(actor)
        with self._uow() as uow:
            cases = uow.cases.in_statuses([STATUS_FOR_QUEUE[queue]], limit=limit)
            items = [
                ReviewQueueItemView(
                    case_id=c.id,
                    case_reference=c.case_reference,
                    status=c.status,
                    review_queue=queue,
                    urgency=c.urgency,
                    procedure_code=c.requested_service.procedure_code,
                    review_requested_at=c.review_requested_at,
                    assigned_reviewer_id=c.assigned_reviewer_id,
                )
                for c in cases
            ]
            # Expedited requests first, then oldest first.
            return sorted(items, key=lambda i: (i.urgency != "EXPEDITED", i.review_requested_at))

    def _require_reviewable(self, case: PreAuthorizationCase, actor: Actor) -> ReviewQueue:
        queue = QUEUE_FOR_STATUS.get(case.status)
        if queue is None:
            raise OperationNotAllowedError(
                f"Case is not awaiting human review (status {case.status})",
                code="CASE_NOT_UNDER_REVIEW",
                details={"status": case.status},
            )
        required_role = ROLE_FOR_QUEUE[queue]
        if not actor.has_role(required_role):
            raise AuthorizationError(
                f"Reviewing cases in {queue} requires role {required_role}",
                code="INSUFFICIENT_REVIEWER_ROLE",
                details={"queue": queue, "required_role": required_role},
            )
        return queue
