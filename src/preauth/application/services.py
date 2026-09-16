from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.case_service import CaseService
from preauth.application.evaluation_service import EvaluationService
from preauth.application.query_service import CaseQueryService
from preauth.application.review_service import ReviewService
from preauth.infrastructure.clock import Clock, SystemClock
from preauth.recommendation.engine import DeterministicRecommendationEngine, RecommendationEngine
from preauth.rules.mock_ruleset import build_mock_rules_engine
from preauth.rules.model import RulesEngine


@dataclass(frozen=True)
class ApplicationServices:
    cases: CaseService
    evaluation: EvaluationService
    review: ReviewService
    queries: CaseQueryService


def build_services(
    session_factory: sessionmaker[Session],
    clock: Clock | None = None,
    rules_engine: RulesEngine | None = None,
    recommendation_engine: RecommendationEngine | None = None,
) -> ApplicationServices:
    clock = clock or SystemClock()
    rules_engine = rules_engine or build_mock_rules_engine()
    recommendation_engine = recommendation_engine or DeterministicRecommendationEngine()
    return ApplicationServices(
        cases=CaseService(session_factory, clock),
        evaluation=EvaluationService(session_factory, clock, rules_engine, recommendation_engine),
        review=ReviewService(session_factory, clock),
        queries=CaseQueryService(session_factory, clock, rules_engine),
    )
