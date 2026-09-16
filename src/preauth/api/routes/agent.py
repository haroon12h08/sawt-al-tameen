"""HTTP transport for the voice-agent toolbox. Adds nothing beyond transport."""

from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Request

from preauth.agent_tools.toolbox import AgentToolbox
from preauth.api.actor import ActorDep
from preauth.api.errors import error_responses
from preauth.api.routes.cases import _doc

router = APIRouter(prefix="/api/v1/agent", tags=["Voice agent tools"])


def _toolbox(request: Request) -> AgentToolbox:
    return request.app.state.toolbox


@router.get(
    "/tools",
    summary="List agent tool definitions",
    description=_doc(
        "Tool names, descriptions, and JSON Schemas for inputs and outputs, suitable for registering with a "
        "tool-calling model. Contains no decision-making tools.",
        "Any authenticated actor.",
        "None.",
    ),
    responses=error_responses(),
)
def list_tools(request: Request, actor: ActorDep) -> list[dict[str, Any]]:
    return _toolbox(request).describe()


@router.post(
    "/tools/{tool_name}",
    summary="Invoke an agent tool",
    description=_doc(
        "Invokes one tool. The body is the tool's arguments object and is validated strictly against that tool's "
        "`input_schema` (see `GET /api/v1/agent/tools`); the response matches its `output_schema`. Errors use the "
        "standard envelope and HTTP status of the underlying operation.",
        "Actor type `VOICE_AGENT` only.",
        "Those of the underlying operation (see the corresponding provider-interaction endpoint).",
    ),
    responses=error_responses(
        r403="VOICE_AGENT_REQUIRED / TRANSITION_NOT_AUTHORIZED",
        r404="TOOL_NOT_FOUND / CASE_NOT_FOUND / RECOMMENDATION_NOT_FOUND",
        r409="INVALID_STATE_TRANSITION / CASE_NOT_EDITABLE / RECOMMENDATION_NOT_READY / CONCURRENT_MODIFICATION",
        r422="TOOL_ARGUMENTS_INVALID / UNKNOWN_PROVIDER / MEMBER_NOT_VERIFIED / ...",
    ),
)
def invoke_tool(
    request: Request,
    actor: ActorDep,
    tool_name: Annotated[str, Path(pattern=r"^[a-z_]{1,64}$")],
    arguments: Annotated[dict[str, Any], Body(default_factory=dict)],
) -> dict[str, Any]:
    return _toolbox(request).invoke(tool_name, actor, arguments)
