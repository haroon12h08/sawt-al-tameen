"""Transport for inbound calls on our own Twilio number (ElevenLabs register-call). Thin: see the service."""

from typing import Annotated

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from preauth.api.errors import ErrorResponse
from preauth.api.routes.cases import _doc
from preauth.application.twilio_inbound_service import INBOUND_PATH, TwilioInboundService

router = APIRouter(tags=["Voice channel (Twilio inbound)"])


@router.post(
    INBOUND_PATH,
    summary="Twilio incoming-call webhook",
    description=_doc(
        "Set as the Voice webhook of your Twilio number (A call comes in → Webhook → HTTP POST). Accepts Twilio's "
        "form-encoded call parameters, registers the call with the ElevenLabs agent "
        "(`POST /v1/convai/twilio/register-call`), and returns the TwiML ElevenLabs produces, as `application/xml`. "
        "If ElevenLabs cannot be reached, returns TwiML that apologises and hangs up rather than an error, so the "
        "caller is never left with Twilio's generic application error.",
        "Header `X-Twilio-Signature`, validated with `TWILIO_AUTH_TOKEN` against `PREAUTH_PUBLIC_BASE_URL` + this "
        "path. Disabled (503) until `TWILIO_AUTH_TOKEN`, `PREAUTH_PUBLIC_BASE_URL`, `ELEVENLABS_API_KEY` and "
        "`PREAUTH_ELEVENLABS_AGENT_ID` are all set.",
        "None. The call becomes an ElevenLabs conversation using the same tools and post-call webhook.",
    ),
    responses={
        200: {"content": {"application/xml": {}}, "description": "TwiML for Twilio"},
        400: {"model": ErrorResponse, "description": "TWILIO_CALL_INVALID (From or To missing)"},
        401: {"model": ErrorResponse, "description": "TWILIO_SIGNATURE_INVALID"},
        503: {"model": ErrorResponse, "description": "CHANNEL_NOT_CONFIGURED"},
    },
    response_class=Response,
)
async def twilio_inbound(
    request: Request,
    x_twilio_signature: Annotated[str | None, Header(alias="X-Twilio-Signature")] = None,
) -> Response:
    service: TwilioInboundService = request.app.state.twilio_inbound
    raw = await request.body()
    # The ElevenLabs call is blocking network I/O; keep it off the event loop.
    result = await run_in_threadpool(service.handle, raw, x_twilio_signature, request.url.query)
    return Response(content=result.twiml, media_type="application/xml")
