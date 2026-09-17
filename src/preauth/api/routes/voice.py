"""Voice channel transport for ElevenLabs: server-tool calls and the post-call webhook.

These routes do not use gateway identity headers. Tool calls authenticate with a bearer token held as an
ElevenLabs workspace secret; the post-call webhook authenticates with an HMAC signature.
"""

import hmac
import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Path, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from preauth.agent_tools.voice_gateway import VoiceToolGateway, VoiceToolResponse
from preauth.api.actor import ActorRequiredError
from preauth.api.errors import ErrorResponse
from preauth.api.routes.cases import _doc, _services
from preauth.application.voice_channel_service import PostCallEvent, PostCallOutcome
from preauth.domain.errors import DomainError

router = APIRouter(prefix="/api/v1/voice", tags=["Voice channel (ElevenLabs)"])

class ChannelNotConfiguredError(DomainError):
    code = "CHANNEL_NOT_CONFIGURED"


class WebhookPayloadInvalidError(DomainError):
    code = "WEBHOOK_PAYLOAD_INVALID"


def _gateway(request: Request) -> VoiceToolGateway:
    return request.app.state.voice_gateway


def _require_voice_token(request: Request, authorization: str | None) -> None:
    expected = request.app.state.settings.voice_agent_token
    if expected is None:
        raise ChannelNotConfiguredError("Voice tools are disabled: PREAUTH_VOICE_AGENT_TOKEN is not set")
    presented = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented.encode(), expected.encode()):
        raise ActorRequiredError("Missing or invalid voice agent bearer token", code="VOICE_TOKEN_INVALID")


@router.post(
    "/tools/{tool_name}",
    summary="Voice platform server-tool call",
    description=_doc(
        "Invokes one agent tool on behalf of the voice platform. The body is the tool's flat parameter object "
        "(see `scripts/elevenlabs_setup.py` or `preauth.agent_tools.elevenlabs`). Empty strings and nulls are "
        "treated as omitted. Business failures return HTTP 200 with `ok=false`, a stable `error.code`, and "
        "`guidance` the agent can act on, because the model must be able to recover mid-call. Unexpected failures "
        "return the standard 500 envelope. Calls carrying `X-Conversation-ID` are linked to the cases they touch.",
        "Header `Authorization: Bearer <PREAUTH_VOICE_AGENT_TOKEN>`. Acts as actor `VOICE_AGENT`.",
        "Those of the underlying tool. Never `APPROVED` or `DENIED`.",
    ),
    responses={
        401: {"model": ErrorResponse, "description": "VOICE_TOKEN_INVALID"},
        503: {"model": ErrorResponse, "description": "CHANNEL_NOT_CONFIGURED"},
        500: {"model": ErrorResponse, "description": "INTERNAL_ERROR"},
    },
)
def voice_tool(
    request: Request,
    tool_name: Annotated[str, Path(pattern=r"^[a-z_]{1,64}$")],
    arguments: Annotated[dict[str, Any], Body(default_factory=dict)],
    authorization: Annotated[str | None, Header()] = None,
    x_conversation_id: Annotated[str | None, Header(description="Voice platform conversation id")] = None,
) -> VoiceToolResponse:
    _require_voice_token(request, authorization)
    return _gateway(request).call(tool_name, arguments, conversation_id=x_conversation_id)


@router.post(
    "/elevenlabs/post-call",
    summary="ElevenLabs post-call webhook",
    description=_doc(
        "Receives `post_call_transcription` events, stores the transcript and analysis as an immutable call "
        "record, and adds a `CALL_RECORDED` audit event to every case the conversation touched. Idempotent per "
        "conversation (platform retries are acknowledged, not duplicated). Other event types are acknowledged "
        "and ignored.",
        "Header `elevenlabs-signature` (HMAC-SHA256 with `PREAUTH_ELEVENLABS_WEBHOOK_SECRET`).",
        "None. Human sign-off on affected cases becomes possible once all their calls are recorded.",
    ),
    responses={
        400: {"model": ErrorResponse, "description": "WEBHOOK_PAYLOAD_INVALID"},
        401: {"model": ErrorResponse, "description": "WEBHOOK_SIGNATURE_INVALID"},
        503: {"model": ErrorResponse, "description": "CHANNEL_NOT_CONFIGURED"},
        500: {"model": ErrorResponse, "description": "INTERNAL_ERROR"},
    },
)
async def post_call_webhook(
    request: Request,
    elevenlabs_signature_header: Annotated[str | None, Header(alias="elevenlabs-signature")] = None,
) -> PostCallOutcome:
    secret = request.app.state.settings.elevenlabs_webhook_secret
    if secret is None:
        raise ChannelNotConfiguredError("Post-call webhook is disabled: PREAUTH_ELEVENLABS_WEBHOOK_SECRET is not set")
    raw = await request.body()
    voice = _services(request).voice
    voice.verify_webhook_signature(raw, elevenlabs_signature_header, secret)
    try:
        event = PostCallEvent.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as e:
        raise WebhookPayloadInvalidError("Webhook body is not a valid event", details={"reason": str(e)[:500]}) from e
    # Storage is synchronous database work; keep it off the event loop.
    return await run_in_threadpool(voice.record_post_call, event)



