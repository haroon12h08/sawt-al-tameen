"""Human-review policy: who may decide in which queue, and how decisions relate to recommendations."""

from preauth.domain.enums import (
    CaseStatus,
    HumanDecisionType,
    RecommendationOutcome,
    ReviewerRole,
    ReviewQueue,
)

QUEUE_FOR_STATUS: dict[CaseStatus, ReviewQueue] = {
    CaseStatus.PENDING_HUMAN_REVIEW: ReviewQueue.CLINICAL_REVIEW,
    CaseStatus.ESCALATED: ReviewQueue.MEDICAL_DIRECTOR_REVIEW,
}

ROLE_FOR_QUEUE: dict[ReviewQueue, ReviewerRole] = {
    ReviewQueue.CLINICAL_REVIEW: ReviewerRole.CLINICAL_REVIEWER,
    ReviewQueue.MEDICAL_DIRECTOR_REVIEW: ReviewerRole.MEDICAL_DIRECTOR,
}

DECISION_TARGET_STATUS: dict[HumanDecisionType, CaseStatus] = {
    HumanDecisionType.APPROVE: CaseStatus.APPROVED,
    HumanDecisionType.DENY: CaseStatus.DENIED,
    HumanDecisionType.REQUEST_INFORMATION: CaseStatus.PENDING_INFORMATION,
    HumanDecisionType.ESCALATE: CaseStatus.ESCALATED,
}

_DECISION_MATCHING_RECOMMENDATION: dict[RecommendationOutcome, HumanDecisionType] = {
    RecommendationOutcome.RECOMMEND_APPROVAL: HumanDecisionType.APPROVE,
    RecommendationOutcome.RECOMMEND_DENIAL: HumanDecisionType.DENY,
    RecommendationOutcome.REQUEST_MORE_INFORMATION: HumanDecisionType.REQUEST_INFORMATION,
    RecommendationOutcome.ESCALATE: HumanDecisionType.ESCALATE,
}

FINAL_DECISIONS = frozenset({HumanDecisionType.APPROVE, HumanDecisionType.DENY})


def routing_status_for(recommendation: RecommendationOutcome) -> CaseStatus:
    """Where a case goes when submitted for human review."""
    if recommendation is RecommendationOutcome.ESCALATE:
        return CaseStatus.ESCALATED
    return CaseStatus.PENDING_HUMAN_REVIEW


def is_override(recommendation: RecommendationOutcome, decision: HumanDecisionType) -> bool:
    """True when the human decision departs from a position the system took.

    An ESCALATE recommendation expresses no position on the outcome (it asks for human judgement), so
    resolving it is never an override. For every other recommendation, any decision other than the matching
    one is an override.
    """
    if recommendation is RecommendationOutcome.ESCALATE:
        return False
    return _DECISION_MATCHING_RECOMMENDATION[recommendation] is not decision
