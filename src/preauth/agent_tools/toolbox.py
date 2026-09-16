"""Voice-agent tool boundary.

The future conversational agent operates the system only through these tools. Each tool is a thin, typed adapter
over an application service: no business rules live here and none may be added here.

Deliberately absent: any tool that assigns reviewers, records human decisions, closes decided cases, or reads the
internal audit trail. The agent can route a case to humans; it can never decide one.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from preauth.application.commands import CaseInformationUpdate, CreateCaseCommand, StrictModel
from preauth.application.services import ApplicationServices
from preauth.application.views import (
    CaseStatusView,
    CaseView,
    EvaluationResultView,
    RecommendationView,
    RequiredInformationView,
)
from preauth.domain.actors import Actor
from preauth.domain.enums import ActorType
from preauth.domain.errors import AuthorizationError, DomainError, NotFoundError

CaseId = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
# Speech-to-text may produce lower case; normalise before matching.
CaseReference = Annotated[
    str,
    StringConstraints(pattern=r"^PA-[0-9A-Z]{8}$"),
    BeforeValidator(lambda v: v.strip().upper() if isinstance(v, str) else v),
]

RECOMMENDATION_NOTICE = (
    "Internal advisory recommendation only. It is NOT an authorisation decision and must not be communicated to "
    "the caller as an approval or denial. Only an authorised human reviewer can approve or deny a request."
)


class ToolArgumentsInvalidError(DomainError):
    code = "TOOL_ARGUMENTS_INVALID"


# --------------------------------------------------------------------------- tool inputs / outputs


class CaseRef(StrictModel):
    case_id: CaseId


class GetCaseInput(StrictModel):
    case_id: CaseId | None = None
    case_reference: CaseReference | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "GetCaseInput":
        if (self.case_id is None) == (self.case_reference is None):
            raise ValueError("Provide exactly one of case_id or case_reference")
        return self


class SubmitInformationInput(StrictModel):
    case_id: CaseId
    information: CaseInformationUpdate


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


def _get_case(s: ApplicationServices, a: Actor, i: GetCaseInput) -> CaseView:
    case_id = i.case_id or s.queries.find_case_id_by_reference(i.case_reference, a)
    return s.queries.get_case(case_id, a)


def _get_recommendation(s: ApplicationServices, a: Actor, i: CaseRef) -> AgentRecommendation:
    return AgentRecommendation(notice=RECOMMENDATION_NOTICE, recommendation=s.queries.get_latest_recommendation(i.case_id, a))


TOOLS: tuple[Tool, ...] = (
    Tool(
        "create_pre_authorization_case",
        "Open a new pre-authorisation case, optionally with any information already collected from the caller. "
        "Returns the case including its case_reference, which should be read back to the caller.",
        CreateCaseCommand,
        CaseView,
        lambda s, a, i: s.cases.create_case(a, i),
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
        "List what information is still required for the case, and whether each item must come from the "
        "provider (ask the caller) or the insurer (do not ask the caller).",
        CaseRef,
        RequiredInformationView,
        lambda s, a, i: s.queries.get_required_information(i.case_id, a),
    ),
    Tool(
        "submit_information",
        "Record information collected from the caller. Only include fields the caller actually provided. "
        "Identifiers are verified; a verification error means the caller should be asked to confirm the value.",
        SubmitInformationInput,
        CaseView,
        lambda s, a, i: s.cases.update_information(i.case_id, a, i.information),
    ),
    Tool(
        "get_case_status",
        "Get the current status of a case and which statuses can follow.",
        CaseRef,
        CaseStatusView,
        lambda s, a, i: s.queries.get_status(i.case_id, a),
    ),
    Tool(
        "evaluate_case",
        "Submit a case with collected information for validation and rule evaluation. If information is missing "
        "the result lists it; otherwise an internal advisory recommendation is generated.",
        CaseRef,
        EvaluationResultView,
        lambda s, a, i: s.evaluation.submit_for_evaluation(i.case_id, a),
    ),
    Tool(
        "get_recommendation",
        "Retrieve the latest internal advisory recommendation. Never present it to the caller as a decision.",
        CaseRef,
        AgentRecommendation,
        _get_recommendation,
    ),
    Tool(
        "request_human_review",
        "Send an evaluated case to a human reviewer, who will make the authorisation decision.",
        CaseRef,
        CaseStatusView,
        lambda s, a, i: s.review.request_human_review(i.case_id, a),
    ),
)


class AgentToolbox:
    def __init__(self, services: ApplicationServices, tools: tuple[Tool, ...] = TOOLS):
        self._services = services
        self._tools = {t.name: t for t in tools}

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
