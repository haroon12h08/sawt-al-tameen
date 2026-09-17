"""The pre-authorisation desk: the three operations the voice agent can perform.

``verify_caller``        identify the calling organisation and, where relevant, the member.
``check_coverage_rule``  open a case, evaluate the benefit schedule, prepare a recommendation, route it to a human.
``log_transcript``       record what the caller was told, and raise a callback when a human must follow up.

Nothing here decides a case. A recommendation is routed to a review queue; only ``ReviewService`` can record an
approval or denial, and only for an authorised human reviewer.
"""

import logging
from datetime import date

from sqlalchemy.orm import Session, sessionmaker

from preauth.application.callback_service import CallbackService
from preauth.application.case_service import enter_information_collection, require_intake_actor
from preauth.application.commands import (
    CoverageCheckCommand,
    LogTranscriptCommand,
    RequestCallbackCommand,
    VerifyCallerCommand,
)
from preauth.application.evaluation_service import EvaluationService
from preauth.application.review_service import ReviewService
from preauth.application.unit_of_work import UnitOfWork
from preauth.application.views import (
    CallLogView,
    CoverageCheckView,
    OnboardingStatusView,
    ProviderView,
    VerificationView,
    member_view,
    recommendation_view,
)
from preauth.domain.actors import Actor
from preauth.domain.enums import (
    AuditEventType,
    CallbackReason,
    CallerRole,
    CallOutcome,
    CaseStatus,
    DirectoryStatus,
    PolicyStatus,
    RecommendationOutcome,
)
from preauth.domain.errors import AuthorizationError, NotFoundError, OperationNotAllowedError
from preauth.domain.review import QUEUE_FOR_STATUS
from preauth.infrastructure.clock import Clock, new_case_reference, new_id
from preauth.infrastructure.db.models import CallerVerification, CallLog, PreAuthorizationCase
from preauth.infrastructure.observability import bind_case_id, conversation_id_var

logger = logging.getLogger("preauth.desk")

CASE_CALLER_ROLES = frozenset({CallerRole.PROVIDER_STAFF, CallerRole.BROKER})

HEADLINES = {
    RecommendationOutcome.RECOMMEND_APPROVAL: "Recommendation prepared: approve, pending sign-off",
    RecommendationOutcome.RECOMMEND_DENIAL: "Recommendation prepared: decline, pending sign-off",
    RecommendationOutcome.REQUEST_MORE_INFORMATION: "More information needed before this can be assessed",
    RecommendationOutcome.ESCALATE: "Referred to a reviewer; no same-call answer",
}
NEXT_STEPS = {
    RecommendationOutcome.RECOMMEND_APPROVAL:
        "A qualified reviewer confirms the recommendation before any authorisation is issued.",
    RecommendationOutcome.RECOMMEND_DENIAL:
        "A qualified reviewer confirms the recommendation before any decision is issued.",
    RecommendationOutcome.REQUEST_MORE_INFORMATION:
        "Submit the listed documents through the provider portal or claims channel quoting the case reference, "
        "then request the check again.",
    RecommendationOutcome.ESCALATE:
        "A reviewer follows up. Elective outpatient requests are answered within six working hours and elective "
        "inpatient requests within 24 hours.",
}


class DeskService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        clock: Clock,
        evaluation: EvaluationService,
        review: ReviewService,
        callbacks: CallbackService,
    ):
        self._session_factory = session_factory
        self._clock = clock
        self._evaluation = evaluation
        self._review = review
        self._callbacks = callbacks

    def _uow(self) -> UnitOfWork:
        return UnitOfWork(self._session_factory, self._clock)

    # ------------------------------------------------------------------ verify_caller

    def verify_caller(self, actor: Actor, command: VerifyCallerCommand) -> VerificationView:
        """Identify the caller before anything policy-specific is discussed.

        A failed verification is recorded too: it is an auditable event, and the agent must fall back to taking a
        message rather than continuing.
        """
        require_intake_actor(actor)
        with self._uow() as uow:
            provider = member = onboarding = None
            failure_code = failure_reason = None

            if command.caller_role is CallerRole.SUPPLIER:
                application = uow.catalogue.onboarding_application(command.caller_reference)
                if application is None:
                    failure_code, failure_reason = (
                        "UNKNOWN_ONBOARDING_REFERENCE",
                        "No onboarding application matches that reference",
                    )
                else:
                    requirements = {r.requirement_id: r for r in uow.catalogue.onboarding_requirements()}
                    onboarding = OnboardingStatusView(
                        application_id=application.application_id,
                        provider_name=application.provider_name,
                        provider_status=application.provider_status,
                        outstanding_requirements=[
                            {
                                "requirement_id": rid,
                                "name": requirements[rid].name if rid in requirements else rid,
                                "issuing_authority": requirements[rid].issuing_authority if rid in requirements else None,
                                "status": next(
                                    (d["status"] for d in application.documents if d["requirement_id"] == rid), None
                                ),
                            }
                            for rid in application.outstanding
                        ],
                    )
            else:
                provider = uow.catalogue.provider_by_number(command.caller_reference)
                if provider is None:
                    failure_code, failure_reason = (
                        "UNKNOWN_PROVIDER",
                        "That provider number is not in the provider directory",
                    )
                elif provider.directory_status is not DirectoryStatus.ACTIVE:
                    failure_code, failure_reason = (
                        "PROVIDER_NOT_ACTIVE",
                        f"Provider {provider.provider_number} is "
                        f"{provider.directory_status.value.replace('_', ' ').lower()} in the directory",
                    )

            if command.member_policy_number is not None and failure_code is None:
                candidate = uow.catalogue.member_by_policy_number(command.member_policy_number)
                # One message whether the policy is unknown or the date of birth mismatches: no membership probing.
                if candidate is None or candidate.date_of_birth != command.member_date_of_birth:
                    failure_code, failure_reason = (
                        "MEMBER_NOT_VERIFIED",
                        "The policy number and date of birth do not match our records",
                    )
                elif candidate.policy_status is not PolicyStatus.ACTIVE:
                    failure_code, failure_reason = (
                        "POLICY_NOT_ACTIVE",
                        f"Policy {candidate.policy_number} lapsed on "
                        f"{candidate.policy_lapse_date.isoformat() if candidate.policy_lapse_date else 'an earlier date'}",
                    )
                    member = candidate
                else:
                    member = candidate

            authorised = failure_code is None
            verification = CallerVerification(
                id=new_id(),
                conversation_id=conversation_id_var.get(),
                caller_role=command.caller_role,
                organisation_name=command.organisation_name,
                caller_reference=command.caller_reference,
                caller_name=command.caller_name,
                provider_id=provider.id if provider else None,
                member_id=member.id if (member and authorised) else None,
                authorised=authorised,
                failure_code=failure_code,
                verified_at=self._clock.now(),
            )
            uow.voice.add_verification(verification)
            uow.commit()
            logger.info(
                "caller_verified",
                extra={
                    "caller_role": command.caller_role.value,
                    "authorised": authorised,
                    "failure_code": failure_code,
                },
            )
            return VerificationView(
                verification_id=verification.id,
                authorised=authorised,
                caller_role=command.caller_role,
                organisation_name=command.organisation_name,
                caller_reference=command.caller_reference,
                provider=ProviderView.model_validate(provider) if provider else None,
                member=member_view(member) if member else None,
                onboarding=onboarding,
                failure_code=failure_code,
                failure_reason=failure_reason,
                verified_at=verification.verified_at,
            )

    # ------------------------------------------------------------------ check_coverage_rule

    def check_coverage_rule(self, actor: Actor, command: CoverageCheckCommand) -> CoverageCheckView:
        require_intake_actor(actor)
        with self._uow() as uow:
            verification = uow.voice.verification(command.verification_id)
            self._require_authorised_for_coverage(verification)
            case = self._case_for_check(uow, actor, verification, command)
            case_id = case.id
            uow.commit()

        # Evaluation opens its own transaction; it validates, runs the ruleset and prepares a recommendation.
        result = self._evaluation.submit_for_evaluation(case_id, actor)
        recommendation = result.recommendation

        if recommendation is None:
            # Intake was incomplete; the case is waiting for the missing details.
            with self._uow() as uow:
                case = uow.cases.get(case_id)
                return CoverageCheckView(
                    case_reference=case.case_reference,
                    case_id=case.id,
                    status=case.status,
                    outcome=RecommendationOutcome.REQUEST_MORE_INFORMATION,
                    headline=HEADLINES[RecommendationOutcome.REQUEST_MORE_INFORMATION],
                    rationale="The request is incomplete; the listed details are needed before it can be checked.",
                    pre_authorisation_required=None,
                    member_co_payment_percent=None,
                    estimated_cost_aed=case.estimated_cost_aed,
                    missing_information=result.missing_information,
                    escalation_citations=[],
                    sources=[],
                    review_queue=None,
                    next_step=NEXT_STEPS[RecommendationOutcome.REQUEST_MORE_INFORMATION],
                )

        # Anything other than a request for documents goes to a human queue straight away.
        if recommendation.outcome is not RecommendationOutcome.REQUEST_MORE_INFORMATION:
            self._review.request_human_review(case_id, actor)

        with self._uow() as uow:
            case = uow.cases.get(case_id)
            evidence = recommendation.evidence.get("AUTH-001-PRE-AUTHORISATION-REQUIRED", {})
            coverage_evidence = recommendation.evidence.get("COV-002-TIER-COVERS-PROCEDURE", {})
            return CoverageCheckView(
                case_reference=case.case_reference,
                case_id=case.id,
                status=case.status,
                outcome=recommendation.outcome,
                headline=HEADLINES[recommendation.outcome],
                rationale=recommendation.rationale,
                pre_authorisation_required=evidence.get("pre_authorisation_required"),
                member_co_payment_percent=coverage_evidence.get("member_co_payment_percent"),
                estimated_cost_aed=case.estimated_cost_aed,
                missing_information=recommendation.missing_information,
                escalation_citations=recommendation.escalation_citations,
                sources=recommendation.sources,
                review_queue=QUEUE_FOR_STATUS.get(case.status),
                next_step=NEXT_STEPS[recommendation.outcome],
            )

    def _require_authorised_for_coverage(self, verification) -> None:
        if not verification.authorised:
            raise AuthorizationError(
                "The caller was not verified; policy details cannot be discussed",
                code="CALLER_NOT_VERIFIED",
                details={"failure_code": verification.failure_code},
            )
        if verification.caller_role not in CASE_CALLER_ROLES:
            raise AuthorizationError(
                "Only clinics and brokers acting for a provider may request a coverage check",
                code="CALLER_ROLE_NOT_ELIGIBLE",
                details={"caller_role": verification.caller_role},
            )
        if verification.member_id is None:
            raise OperationNotAllowedError(
                "The member must be verified before a coverage check",
                code="MEMBER_NOT_VERIFIED",
                details={"verification_id": verification.id},
            )

    def _case_for_check(
        self, uow: UnitOfWork, actor: Actor, verification, command: CoverageCheckCommand
    ) -> PreAuthorizationCase:
        if command.case_reference is not None:
            case = uow.cases.get(uow.cases.id_for_reference(command.case_reference))
            bind_case_id(case.id)
            if case.member_id != verification.member_id:
                raise AuthorizationError(
                    "That case belongs to a different member",
                    code="CASE_MEMBER_MISMATCH",
                    details={"case_reference": command.case_reference},
                )
            self._apply_request(uow, case, actor, command, changed=True)
            return case

        now = self._clock.now()
        case = PreAuthorizationCase(
            id=new_id(),
            case_reference=new_case_reference(),
            status=CaseStatus.RECEIVED,
            created_by_actor_type=actor.type,
            created_by_actor_id=actor.id,
            verification_id=verification.id,
            caller_name=verification.caller_name,
            caller_role=verification.caller_role,
            caller_organisation=verification.organisation_name,
            provider_id=verification.provider_id,
            member_id=verification.member_id,
            created_at=now,
            updated_at=now,
            documents=[],
        )
        bind_case_id(case.id)
        uow.cases.add(case)
        uow.flush()
        uow.audit.record(
            case.id,
            AuditEventType.CASE_CREATED,
            actor,
            {
                "case_reference": case.case_reference,
                "verification_id": verification.id,
                "caller_role": verification.caller_role,
                "caller_organisation": verification.organisation_name,
            },
        )
        self._apply_request(uow, case, actor, command, changed=False)
        return case

    def _apply_request(
        self, uow: UnitOfWork, case: PreAuthorizationCase, actor: Actor, command: CoverageCheckCommand, *, changed: bool
    ) -> None:
        from preauth.application.case_service import require_editable

        require_editable(case)
        fields = {
            "procedure_code": command.procedure_code,
            "treatment_date": command.treatment_date,
            "estimated_cost_aed": command.estimated_cost_aed,
            "urgency": command.urgency,
            "diagnosis_code": command.diagnosis_code,
            "clinical_summary": command.clinical_summary,
        }
        collected, modified = {}, {}
        for name, value in fields.items():
            if value is None:
                continue
            previous = getattr(case, name)
            if previous == value:
                continue
            setattr(case, name, value)
            if previous is None:
                collected[name] = value
            else:
                modified[name] = {"previous": previous, "new": value}

        if collected:
            uow.audit.record(case.id, AuditEventType.INFORMATION_COLLECTED, actor, {"fields": collected})
        if modified:
            uow.audit.record(case.id, AuditEventType.INFORMATION_MODIFIED, actor, {"fields": modified})
        if collected or modified or changed:
            enter_information_collection(uow, case, actor, reason="coverage_check_requested")
        case.updated_at = self._clock.now()
        uow.flush()

    # ------------------------------------------------------------------ log_transcript

    def log_transcript(self, actor: Actor, command: LogTranscriptCommand) -> CallLogView:
        """Record the call outcome. For escalations and unverified callers, also raise a human callback."""
        require_intake_actor(actor)
        case_id = None
        caller_role = None
        callback_id = None

        with self._uow() as uow:
            verification = (
                uow.voice.verification(command.verification_id) if command.verification_id else None
            )
            if verification is not None:
                caller_role = verification.caller_role
            if command.case_reference is not None:
                case_id = uow.cases.id_for_reference(command.case_reference)
                bind_case_id(case_id)

        needs_callback = command.outcome_communicated in (
            CallOutcome.ESCALATED,
            CallOutcome.CALLER_NOT_VERIFIED,
            CallOutcome.OUT_OF_SCOPE,
            CallOutcome.ONBOARDING_ENQUIRY,
        )
        if needs_callback and command.callback_phone:
            callback = self._callbacks.request_callback(
                actor,
                RequestCallbackCommand(
                    case_id=case_id,
                    caller_name=command.caller_name or "Caller",
                    caller_role=caller_role or CallerRole.OTHER,
                    callback_phone=command.callback_phone,
                    preferred_language=command.preferred_language,
                    reason=command.callback_reason or _DEFAULT_CALLBACK_REASON[command.outcome_communicated],
                    summary=command.summary,
                ),
            )
            callback_id = callback.id

        with self._uow() as uow:
            log = CallLog(
                id=new_id(),
                reference=new_case_reference().replace("PA-", "CL-"),
                conversation_id=conversation_id_var.get(),
                case_id=case_id,
                callback_id=callback_id,
                caller_role=caller_role,
                outcome_communicated=command.outcome_communicated.value,
                summary=command.summary,
                logged_at=self._clock.now(),
            )
            uow.voice.add_call_log(log)
            uow.flush()
            if case_id is not None:
                uow.audit.record(
                    case_id,
                    AuditEventType.CALL_SUMMARY_LOGGED,
                    actor,
                    {
                        "call_log_reference": log.reference,
                        "outcome_communicated": command.outcome_communicated,
                        "summary": command.summary,
                        "callback_id": callback_id,
                    },
                )
            uow.commit()
            logger.info(
                "call_logged",
                extra={"outcome": command.outcome_communicated.value, "has_callback": callback_id is not None},
            )
            return CallLogView.model_validate(log)


_DEFAULT_CALLBACK_REASON = {
    CallOutcome.ESCALATED: CallbackReason.NON_STANDARD_REQUEST,
    CallOutcome.CALLER_NOT_VERIFIED: CallbackReason.CALLER_REQUESTED_HUMAN,
    CallOutcome.OUT_OF_SCOPE: CallbackReason.OTHER,
    CallOutcome.ONBOARDING_ENQUIRY: CallbackReason.SUPPLIER_ENQUIRY,
}
