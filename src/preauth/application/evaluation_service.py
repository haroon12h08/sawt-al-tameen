"""Validation, rule evaluation, and recommendation generation.

The evaluation pipeline runs inside one transaction: either the full sequence of state changes, evaluation
records, recommendation, and audit events is committed, or none of it is.
"""

import logging

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.case_service import require_intake_actor
from preauth.application.rule_context import build_rule_context, missing_intake
from preauth.application.unit_of_work import UnitOfWork
from preauth.application.views import EvaluationResultView, recommendation_view
from preauth.domain.actors import SYSTEM_ACTOR, Actor
from preauth.domain.enums import AuditEventType, CaseStatus, RecommendationOutcome, RuleOutcome
from preauth.infrastructure.clock import Clock, new_id
from preauth.infrastructure.db.models import (
    PreAuthorizationCase,
    Recommendation,
    RuleDefinition,
    RuleEvaluation,
    RuleResultRecord,
)
from preauth.infrastructure.observability import bind_case_id
from preauth.recommendation.engine import RecommendationEngine
from preauth.rules.model import RulesEngine

logger = logging.getLogger("preauth.evaluation")


class EvaluationService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
        rules_engine: RulesEngine,
        recommendation_engine: RecommendationEngine,
    ):
        self._session_factory = session_factory
        self._clock = clock
        self._rules = rules_engine
        self._recommender = recommendation_engine

    def submit_for_evaluation(self, case_id: str, actor: Actor) -> EvaluationResultView:
        require_intake_actor(actor)
        bind_case_id(case_id)
        with UnitOfWork(self._session_factory, self._clock) as uow:
            case = uow.cases.get(case_id)
            uow.transition(case, CaseStatus.VALIDATION, actor, reason="submitted_for_evaluation")
            system_data = {"triggered_by": {"actor_type": actor.type, "actor_id": actor.id}}

            missing = missing_intake(case)
            if missing:
                uow.audit.record(
                    case.id,
                    AuditEventType.VALIDATION_FAILED,
                    SYSTEM_ACTOR,
                    {"missing_information": [m.model_dump(mode="json") for m in missing]},
                )
                uow.transition(
                    case, CaseStatus.PENDING_INFORMATION, SYSTEM_ACTOR, reason="intake_incomplete", data=system_data
                )
                uow.commit()
                return EvaluationResultView(
                    case_id=case.id,
                    status=case.status,
                    validation_passed=False,
                    missing_information=missing,
                    recommendation=None,
                )

            uow.audit.record(case.id, AuditEventType.VALIDATION_PASSED, SYSTEM_ACTOR, {})
            uow.transition(case, CaseStatus.RULE_EVALUATION, SYSTEM_ACTOR, reason="intake_complete", data=system_data)

            recommendation = self._evaluate(uow, case, actor)

            target = (
                CaseStatus.PENDING_INFORMATION
                if recommendation.outcome is RecommendationOutcome.REQUEST_MORE_INFORMATION
                else CaseStatus.RECOMMENDATION_READY
            )
            uow.transition(
                case,
                target,
                SYSTEM_ACTOR,
                reason="recommendation_generated",
                data={**system_data, "recommendation_id": recommendation.id, "recommendation": recommendation.outcome},
            )
            uow.commit()
            rec_view = recommendation_view(uow.evaluations.latest_recommendation(case.id))
            return EvaluationResultView(
                case_id=case.id,
                status=case.status,
                validation_passed=True,
                missing_information=rec_view.missing_information,
                recommendation=rec_view,
            )

    def _evaluate(self, uow: UnitOfWork, case: PreAuthorizationCase, actor: Actor) -> Recommendation:
        ctx = build_rule_context(uow, case)
        outcome = self._rules.evaluate(ctx)
        now = self._clock.now()

        registered_at = now
        for rule in self._rules.rules:
            uow.evaluations.ensure_rule_definition(
                RuleDefinition(
                    rule_id=rule.rule_id,
                    version=rule.version,
                    description=rule.description,
                    category=rule.category,
                    first_registered_at=registered_at,
                )
            )

        evaluation = RuleEvaluation(
            id=new_id(),
            case_id=case.id,
            sequence=uow.evaluations.next_evaluation_sequence(case.id),
            engine_name=outcome.engine_name,
            engine_version=outcome.engine_version,
            input_snapshot=ctx.model_dump(mode="json"),
            evaluated_at=now,
            triggered_by_actor_type=actor.type,
            triggered_by_actor_id=actor.id,
            results=[
                RuleResultRecord(
                    id=new_id(),
                    position=position,
                    rule_id=r.rule_id,
                    rule_version=r.rule_version,
                    outcome=r.outcome,
                    explanation=r.explanation,
                    evidence=r.model_dump(mode="json")["evidence"],
                    missing_information=[mi.model_dump(mode="json") for mi in r.missing_information],
                )
                for position, r in enumerate(outcome.results)
            ],
        )
        uow.evaluations.add_evaluation(evaluation)
        uow.flush()
        uow.audit.record(
            case.id,
            AuditEventType.RULES_EVALUATED,
            SYSTEM_ACTOR,
            {
                "evaluation_id": evaluation.id,
                "ruleset_name": outcome.engine_name,
                "ruleset_version": outcome.engine_version,
                "results": [
                    {"rule_id": r.rule_id, "rule_version": r.rule_version, "outcome": r.outcome}
                    for r in outcome.results
                ],
                "counts": {o.value: sum(1 for r in outcome.results if r.outcome is o) for o in RuleOutcome},
            },
        )

        draft = self._recommender.recommend(outcome.results, ctx)
        recommendation = Recommendation(
            id=new_id(),
            case_id=case.id,
            evaluation_id=evaluation.id,
            outcome=draft.outcome,
            rationale=draft.rationale,
            determining_rule_ids=list(draft.determining_rule_ids),
            evidence=draft.model_dump(mode="json")["evidence"],
            missing_information=[mi.model_dump(mode="json") for mi in draft.missing_information],
            engine_name=draft.engine_name,
            engine_version=draft.engine_version,
            generated_at=self._clock.now(),
        )
        uow.evaluations.add_recommendation(recommendation)
        uow.flush()
        uow.audit.record(
            case.id,
            AuditEventType.RECOMMENDATION_GENERATED,
            SYSTEM_ACTOR,
            {
                "recommendation_id": recommendation.id,
                "evaluation_id": evaluation.id,
                "outcome": draft.outcome,
                "determining_rule_ids": list(draft.determining_rule_ids),
                "engine_name": draft.engine_name,
                "engine_version": draft.engine_version,
            },
        )
        logger.info(
            "recommendation_generated",
            extra={"recommendation": draft.outcome.value, "ruleset_version": outcome.engine_version},
        )
        return recommendation
