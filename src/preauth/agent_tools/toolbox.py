"""Voice-agent tool boundary.

The future conversational agent operates the system only through these tools. Each tool is a thin, typed adapter
over an application service: no business rules live here and none may be added here.

Tool inputs are deliberately flat (strings, integers, enums) with a description on every field: voice platforms
such as ElevenLabs accept only simple parameter schemas, and the model needs guidance for each value it fills in.

Deliberately absent: any tool that assigns reviewers, records human decisions, closes decided cases, or reads the
internal audit trail. The agent can route a case to humans; it can never decide one.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from preauth.application.commands import (
    CaseInformationUpdate,
    CreateCaseCommand,
    RequestCallbackCommand,
    StrictModel,
)
from preauth.application.services import ApplicationServices
from preauth.application.views import (
    CallbackView,
    CaseStatusView,
    CaseView,
    EvaluationResultView,
    RecommendationView,
    RequiredInformationView,
)
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType, CallbackReason, CallerRole, PlaceOfService, Urgency
from preauth.domain.errors import AuthorizationError, DomainError, NotFoundError

CaseId = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]


def _upper(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


# Speech-to-text and models may produce lower case; normalise before matching.
CaseReference = Annotated[str, StringConstraints(pattern=r"^PA-[0-9A-Z]{8}$"), BeforeValidator(_upper)]
Code = Annotated[str, BeforeValidator(_upper)]

RECOMMENDATION_NOTICE = (
    "Internal advisory recommendation only. It is NOT an authorisation decision and must not be communicated to "
    "the caller as an approval or denial. Only an authorised human reviewer can approve or deny a request."
)

CASE_ID_DESCRIPTION = "The case_id (UUID) returned by create_pre_authorization_case or get_case."


class ToolArgumentsInvalidError(DomainError):
    code = "TOOL_ARGUMENTS_INVALID"


# --------------------------------------------------------------------------- tool inputs


class CaseRef(StrictModel):
    case_id: CaseId = Field(description=CASE_ID_DESCRIPTION)


class GetCaseInput(StrictModel):
    case_id: CaseId | None = Field(default=None, description=CASE_ID_DESCRIPTION + " Omit if using case_reference.")
    case_reference: CaseReference | None = Field(
        default=None,
        description="The case reference the caller quotes, format PA- followed by 8 letters/digits, e.g. PA-7K3M9Q2B. "
        "Omit if using case_id.",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "GetCaseInput":
        if (self.case_id is None) == (self.case_reference is None):
            raise ValueError("Provide exactly one of case_id or case_reference")
        return self


class CaseInformationFields(StrictModel):
    """Information collected from the caller. Every field is optional; include only what the caller said."""

    caller_name: str | None = Field(default=None, description="Name of the person calling.")
    caller_role: CallerRole | None = Field(
        default=None,
        description="PROVIDER_STAFF if calling from a clinic or hospital; BROKER if a broker acting for a provider.",
    )
    provider_number: Code | None = Field(
        default=None, description="Requesting provider's number, format PRV- followed by 6 digits, e.g. PRV-100234."
    )
    patient_member_id: Code | None = Field(
        default=None,
        description="Patient's member ID, format MBR-dddd-dd, e.g. MBR-5001-01. Always send together with "
        "patient_date_of_birth.",
    )
    patient_date_of_birth: date | None = Field(
        default=None, description="Patient's date of birth, format YYYY-MM-DD. Send together with patient_member_id."
    )
    policy_number: Code | None = Field(
        default=None, description="Patient's policy number, format POL- followed by 6 digits, e.g. POL-000101."
    )
    procedure_code: Code | None = Field(
        default=None, description="Requested procedure code, format PROC-NAME, e.g. PROC-MRI-KNEE."
    )
    requested_service_date: date | None = Field(
        default=None, description="Planned date of the procedure, format YYYY-MM-DD. Must be today or later."
    )
    place_of_service: PlaceOfService | None = Field(
        default=None, description="Where the procedure will happen: INPATIENT, OUTPATIENT or OFFICE."
    )
    diagnosis_code: Code | None = Field(
        default=None, description="Primary diagnosis as an ICD-10 code with the dot, e.g. M23.221."
    )
    diagnosis_description: str | None = Field(default=None, description="Diagnosis in plain words, if given.")
    urgency: Urgency | None = Field(
        default=None, description="STANDARD, or EXPEDITED if the caller says the request is clinically urgent."
    )
    conservative_treatment_weeks: int | None = Field(
        default=None, description="Number of weeks of conservative treatment (e.g. physiotherapy) already completed."
    )
    clinical_summary: str | None = Field(
        default=None, description="Short clinical justification in the caller's words."
    )

    @model_validator(mode="after")
    def _patient_pair(self) -> "CaseInformationFields":
        if (self.patient_member_id is None) != (self.patient_date_of_birth is None):
            raise ValueError("patient_member_id and patient_date_of_birth must be provided together")
        return self

    def to_update(self) -> CaseInformationUpdate | None:
        values = {k: v for k, v in self.model_dump(exclude_none=True).items() if k != "case_id"}
        member_id = values.pop("patient_member_id", None)
        dob = values.pop("patient_date_of_birth", None)
        if member_id is not None:
            values["patient"] = {"member_id": member_id, "date_of_birth": dob}
        if not values:
            return None
        return CaseInformationUpdate(**values)


class SubmitInformationInput(CaseInformationFields):
    case_id: CaseId = Field(description=CASE_ID_DESCRIPTION)


class RequestCallbackInput(StrictModel):
    case_id: CaseId | None = Field(
        default=None, description=CASE_ID_DESCRIPTION + " Omit if no case has been opened."
    )
    caller_name: str = Field(description="Name of the person calling.")
    caller_organisation: str | None = Field(default=None, description="Clinic, hospital, brokerage or company name.")
    caller_role: CallerRole = Field(
        description="PROVIDER_STAFF, BROKER, SUPPLIER (e.g. a supplier asking about onboarding), or OTHER."
    )
    callback_phone: str = Field(
        description="Number to call back in international format with no spaces, e.g. +971501234567."
    )
    preferred_language: str = Field(description="Two-letter language code for the callback, e.g. en, ar, hi, ur.")
    reason: CallbackReason = Field(
        description="NON_STANDARD_REQUEST (not covered by the standard process), CALLER_REQUESTED_HUMAN, "
        "SUPPLIER_ENQUIRY, URGENT_CLINICAL, COMPLAINT, UNSUPPORTED_LANGUAGE, or OTHER."
    )
    summary: str = Field(description="What the caller needs, in one or two sentences, for the staff member.")

    def to_command(self) -> RequestCallbackCommand:
        return RequestCallbackCommand(**self.model_dump(exclude_none=True))


class AgentRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True)

    notice: str
    recommendation: RecommendationView


# --------------------------------------------------------------------------- registry


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_type: Any
    handler: Callable[[ApplicationServices, Actor, Any], BaseModel]


def _convert(build: Callable[[], Any]) -> Any:
    """Run a flat-to-command conversion, reporting failures as invalid tool arguments."""
    try:
        return build()
    except ValidationError as e:
        raise ToolArgumentsInvalidError(
            "Arguments are invalid",
            details={"errors": [{"location": list(err["loc"]), "message": err["msg"]} for err in e.errors()]},
        ) from e


def _create_case(s: ApplicationServices, a: Actor, i: CaseInformationFields) -> CaseView:
    return s.cases.create_case(a, CreateCaseCommand(information=_convert(i.to_update)))


def _submit_information(s: ApplicationServices, a: Actor, i: SubmitInformationInput) -> CaseView:
    update = _convert(i.to_update)
    if update is None:
        raise ToolArgumentsInvalidError("Provide at least one information field", details={"tool": "submit_information"})
    return s.cases.update_information(i.case_id, a, update)


def _get_case(s: ApplicationServices, a: Actor, i: GetCaseInput) -> CaseView:
    case_id = i.case_id or s.queries.find_case_id_by_reference(i.case_reference, a)
    return s.queries.get_case(case_id, a)


def _get_recommendation(s: ApplicationServices, a: Actor, i: CaseRef) -> AgentRecommendation:
    return AgentRecommendation(
        notice=RECOMMENDATION_NOTICE, recommendation=s.queries.get_latest_recommendation(i.case_id, a)
    )


TOOLS: tuple[Tool, ...] = (
    Tool(
        "create_pre_authorization_case",
        "Open a new pre-authorisation case, optionally with information already collected from the caller. "
        "Returns the case with its case_id (use in later tool calls) and case_reference (read it back to the caller).",
        CaseInformationFields,
        CaseView,
        _create_case,
    ),
    Tool(
        "get_case",
        "Retrieve a case by case_id, or by the case_reference the caller quotes.",
        GetCaseInput,
        CaseView,
        _get_case,
    ),
    Tool(
        "get_required_information",
        "List what information is still required for the case. Items with source PROVIDER must be asked from the "
        "caller; items with source INSURER cannot be supplied by the caller.",
        CaseRef,
        RequiredInformationView,
        lambda s, a, i: s.queries.get_required_information(i.case_id, a),
    ),
    Tool(
        "submit_information",
        "Record information collected from the caller on an existing case. Include only fields the caller actually "
        "provided. Identifiers are verified against records; on a verification error ask the caller to confirm.",
        SubmitInformationInput,
        CaseView,
        _submit_information,
    ),
    Tool(
        "get_case_status",
        "Get the current status of a case.",
        CaseRef,
        CaseStatusView,
        lambda s, a, i: s.queries.get_status(i.case_id, a),
    ),
    Tool(
        "evaluate_case",
        "Check the case against the insurance rules once information has been collected. If information is missing "
        "the result lists it; otherwise an internal advisory recommendation is prepared.",
        CaseRef,
        EvaluationResultView,
        lambda s, a, i: s.evaluation.submit_for_evaluation(i.case_id, a),
    ),
    Tool(
        "get_recommendation",
        "Retrieve the latest internal advisory recommendation with the policy sources it relied on. Never present "
        "it to the caller as an approval or denial.",
        CaseRef,
        AgentRecommendation,
        _get_recommendation,
    ),
    Tool(
        "request_human_review",
        "Send an evaluated case to a qualified human reviewer, who makes the authorisation decision.",
        CaseRef,
        CaseStatusView,
        lambda s, a, i: s.review.request_human_review(i.case_id, a),
    ),
    Tool(
        "request_human_callback",
        "Hand the caller to a human: use for requests outside the standard pre-authorisation process, ambiguous "
        "situations, supplier or onboarding enquiries, complaints, urgent clinical concerns, unsupported languages, "
        "or when the caller asks for a person. Returns a callback reference to read back.",
        RequestCallbackInput,
        CallbackView,
        lambda s, a, i: s.callbacks.request_callback(a, _convert(i.to_command)),
    ),
)


class AgentToolbox:
    def __init__(self, services: ApplicationServices, tools: tuple[Tool, ...] = TOOLS):
        self._services = services
        self._tools = {t.name: t for t in tools}

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> list[dict[str, Any]]:
        """Tool definitions in a model-agnostic form (name, description, JSON Schemas)."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_model.model_json_schema(),
                "output_schema": TypeAdapter(t.output_type).json_schema(),
            }
            for t in self._tools.values()
        ]

    def invoke(self, name: str, actor: Actor, arguments: dict[str, Any]) -> dict[str, Any]:
        if actor.type is not ActorType.VOICE_AGENT:
            raise AuthorizationError(
                "Agent tools may only be invoked by a VOICE_AGENT actor",
                code="VOICE_AGENT_REQUIRED",
                details={"actor_type": actor.type},
            )
        tool = self._tools.get(name)
        if tool is None:
            raise NotFoundError(
                f"Unknown tool {name!r}", code="TOOL_NOT_FOUND", details={"available_tools": self.tool_names}
            )
        try:
            parsed = tool.input_model.model_validate(arguments)
        except ValidationError as e:
            raise ToolArgumentsInvalidError(
                f"Arguments for {name} are invalid",
                details={
                    "tool": name,
                    "errors": [{"location": list(err["loc"]), "message": err["msg"]} for err in e.errors()],
                },
            ) from e
        return tool.handler(self._services, actor, parsed).model_dump(mode="json")
