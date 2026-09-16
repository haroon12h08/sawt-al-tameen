"""Explicit case state machine.

Every permitted transition is listed here together with the actor types allowed to perform it. Anything not
listed is rejected. Final authorisation outcomes (APPROVED, DENIED) are reachable only by HUMAN_REVIEWER actors.
"""

from preauth.domain.enums import ActorType, CaseStatus

S = CaseStatus
_SUBMITTERS = frozenset({ActorType.PROVIDER_PORTAL, ActorType.VOICE_AGENT})
_SYSTEM = frozenset({ActorType.SYSTEM})
_ROUTERS = frozenset({ActorType.PROVIDER_PORTAL, ActorType.VOICE_AGENT, ActorType.SYSTEM})
_HUMAN = frozenset({ActorType.HUMAN_REVIEWER})
_CLOSERS_AFTER_DECISION = frozenset({ActorType.SYSTEM, ActorType.HUMAN_REVIEWER})

TRANSITIONS: dict[tuple[CaseStatus, CaseStatus], frozenset[ActorType]] = {
    # Information gathering
    (S.RECEIVED, S.INFORMATION_COLLECTION): _SUBMITTERS,
    (S.PENDING_INFORMATION, S.INFORMATION_COLLECTION): _SUBMITTERS,
    (S.RECOMMENDATION_READY, S.INFORMATION_COLLECTION): _SUBMITTERS,
    # Evaluation pipeline
    (S.INFORMATION_COLLECTION, S.VALIDATION): _SUBMITTERS,
    (S.VALIDATION, S.PENDING_INFORMATION): _SYSTEM,
    (S.VALIDATION, S.RULE_EVALUATION): _SYSTEM,
    (S.RULE_EVALUATION, S.RECOMMENDATION_READY): _SYSTEM,
    (S.RULE_EVALUATION, S.PENDING_INFORMATION): _SYSTEM,
    # Routing to humans (routing is not a decision)
    (S.RECOMMENDATION_READY, S.PENDING_HUMAN_REVIEW): _ROUTERS,
    (S.RECOMMENDATION_READY, S.ESCALATED): _ROUTERS,
    # Human decisions
    (S.PENDING_HUMAN_REVIEW, S.APPROVED): _HUMAN,
    (S.PENDING_HUMAN_REVIEW, S.DENIED): _HUMAN,
    (S.PENDING_HUMAN_REVIEW, S.PENDING_INFORMATION): _HUMAN,
    (S.PENDING_HUMAN_REVIEW, S.ESCALATED): _HUMAN,
    (S.ESCALATED, S.APPROVED): _HUMAN,
    (S.ESCALATED, S.DENIED): _HUMAN,
    (S.ESCALATED, S.PENDING_INFORMATION): _HUMAN,
    # Closure
    (S.RECEIVED, S.CLOSED): _SUBMITTERS,
    (S.INFORMATION_COLLECTION, S.CLOSED): _SUBMITTERS,
    (S.PENDING_INFORMATION, S.CLOSED): _SUBMITTERS,
    (S.RECOMMENDATION_READY, S.CLOSED): _SUBMITTERS,
    (S.APPROVED, S.CLOSED): _CLOSERS_AFTER_DECISION,
    (S.DENIED, S.CLOSED): _CLOSERS_AFTER_DECISION,
}

HUMAN_DECISION_STATES = frozenset({S.APPROVED, S.DENIED})
TERMINAL_STATES = frozenset({S.CLOSED})

# States in which the provider side may add or change information / documents.
EDITABLE_STATES = frozenset(
    {S.RECEIVED, S.INFORMATION_COLLECTION, S.PENDING_INFORMATION, S.RECOMMENDATION_READY}
)

# Safety net: no non-human actor may ever reach a human-decision state, regardless of table edits.
for (_src, _dst), _actors in TRANSITIONS.items():
    if _dst in HUMAN_DECISION_STATES and _actors != _HUMAN:
        raise RuntimeError(f"Transition {_src}->{_dst} must be restricted to HUMAN_REVIEWER")


def allowed_targets(current: CaseStatus) -> frozenset[CaseStatus]:
    return frozenset(dst for (src, dst) in TRANSITIONS if src == current)


def is_transition_allowed(current: CaseStatus, target: CaseStatus, actor_type: ActorType) -> bool:
    return actor_type in TRANSITIONS.get((current, target), frozenset())


def assert_transition(current: CaseStatus, target: CaseStatus, actor_type: ActorType) -> None:
    from preauth.domain.errors import AuthorizationError, InvalidStateTransitionError

    permitted_actors = TRANSITIONS.get((current, target))
    if permitted_actors is None:
        raise InvalidStateTransitionError(
            f"Transition {current} -> {target} is not permitted",
            details={
                "from_status": current,
                "to_status": target,
                "allowed_targets": sorted(allowed_targets(current)),
            },
        )
    if actor_type not in permitted_actors:
        raise AuthorizationError(
            f"Actor type {actor_type} may not perform transition {current} -> {target}",
            details={
                "from_status": current,
                "to_status": target,
                "actor_type": actor_type,
                "permitted_actor_types": sorted(permitted_actors),
            },
            code="TRANSITION_NOT_AUTHORIZED",
        )
