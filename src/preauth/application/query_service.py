"""Read-side use cases. No state changes and no audit events."""

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.review_service import require_human_reviewer
from preauth.application.rule_context import INTAKE_REQUIREMENTS, build_rule_context, missing_intake
from preauth.application.unit_of_work import UnitOfWork
from preauth.application.views import (
    AuditEventView,
    CaseStatusView,
    CaseView,
    IntakeRequirementView,
    RecommendationView,
    RequiredInformationView,
    case_view,
    recommendation_view,
    status_view,
)
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType
from preauth.domain.errors import NotFoundError
from preauth.domain.review import FINAL_DECISIONS
from preauth.infrastructure.clock import Clock
from preauth.infrastructure.observability import bind_case_id
from preauth.rules.model import RulesEngine


class CaseQueryService:
    def __init__(self, session_factory: sessionmaker[Session], clock: Clock, rules_engine: RulesEngine):
        self._session_factory = session_factory
        self._clock = clock
        self._rules = rules_engine

    def _uow(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory, self._clock)

    def get_case(self, case_id: str, actor: Actor) -> CaseView:
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            return case_view(case, uow.catalogue.procedure(case.procedure_code) if case.procedure_code else None)

    def find_case_id_by_reference(self, case_reference: str, actor: Actor) -> str:
        with self._uow() as uow:
            return uow.cases.id_for_reference(case_reference)

    def get_status(self, case_id: str, actor: Actor) -> CaseStatusView:
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            final = next(
                (d for d in reversed(uow.reviews.decisions(case_id)) if d.decision in FINAL_DECISIONS), None
            )
            return status_view(case, final)

    def get_required_information(self, case_id: str, actor: Actor) -> RequiredInformationView:
        """What is still needed before (or after) evaluation.

        When intake is complete, the ruleset is run as a dry run (nothing persisted, no audit event) to discover
        rule-level requirements such as supporting documents.
        """
        bind_case_id(case_id)
        with self._uow() as uow:
            case = uow.cases.get(case_id)
            intake_missing = missing_intake(case)
            requirements = [
                IntakeRequirementView(code=r.code, description=r.description, provided=r.is_provided(case))
                for r in INTAKE_REQUIREMENTS
            ]
            if intake_missing:
                return RequiredInformationView(
                    case_id=case.id,
                    status=case.status,
                    intake_complete=False,
                    intake_requirements=requirements,
                    missing_information=intake_missing,
                    ruleset_name=None,
                    ruleset_version=None,
                )
            outcome = self._rules.evaluate(build_rule_context(uow, case))
            seen: dict[str, object] = {}
            for result in outcome.results:
                for missing in result.missing_information:
                    seen.setdefault(missing.code, missing)
            return RequiredInformationView(
                case_id=case.id,
                status=case.status,
                intake_complete=True,
                intake_requirements=requirements,
                missing_information=list(seen.values()),
                ruleset_name=outcome.engine_name,
                ruleset_version=outcome.engine_version,
            )

    def get_latest_recommendation(self, case_id: str, actor: Actor) -> RecommendationView:
        bind_case_id(case_id)
        with self._uow() as uow:
            uow.cases.get(case_id)
            rec = uow.evaluations.latest_recommendation(case_id)
            if rec is None:
                raise NotFoundError(
                    "No recommendation has been generated for this case",
                    code="RECOMMENDATION_NOT_FOUND",
                    details={"case_id": case_id},
                )
            return recommendation_view(rec)

    def get_audit_history(self, case_id: str, actor: Actor) -> list[AuditEventView]:
        if actor.type is not ActorType.SYSTEM:
            require_human_reviewer(actor)
        bind_case_id(case_id)
        with self._uow() as uow:
            uow.cases.get(case_id)
            return [AuditEventView.model_validate(e) for e in uow.audit_events.for_case(case_id)]

