"""Inbound calls on our own Twilio number, handed to the ElevenLabs agent through register-call.

Twilio posts the incoming call here; we verify Twilio's signature, register the call with the agent, and hand
Twilio back the TwiML ElevenLabs returns. From then on the call is an ordinary ElevenLabs conversation: the same
three tools, the same post-call webhook, the same rules and review queue. Nothing here touches a case.

Security: the endpoint refuses to run unless it can check signatures, and it checks them against the public URL
Twilio was configured with. Logs carry the CallSid and the last four digits of each number, never tokens.
"""

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from preauth.domain.errors import DomainError
from preauth.infrastructure import twilio_signature
from preauth.infrastructure.elevenlabs_register_call import (
    ElevenLabsRegisterCallClient,
    RegisterCallClient,
    RegisterCallError,
)
from preauth.infrastructure.settings import Settings

logger = logging.getLogger("preauth.voice.twilio")

INBOUND_PATH = "/api/v1/voice/twilio/inbound"

# Said to the caller when the agent cannot be reached. Twilio's own error would be a generic "application error".
FALLBACK_TWIML = (
    '<?xml version="1.0" encoding="UTF-8"?><Response>'
    "<Say>Sorry, the Sawt Assurance pre-authorisation line is unavailable right now. Please try again shortly."
    "</Say><Hangup/></Response>"
)


class TwilioChannelNotConfiguredError(DomainError):
    code = "CHANNEL_NOT_CONFIGURED"


class InboundCallInvalidError(DomainError):
    code = "TWILIO_CALL_INVALID"


def _tail(number: str) -> str:
    """Enough of a phone number to correlate with Twilio's logs, not enough to identify the caller."""
    digits = "".join(c for c in number if c.isdigit())
    return f"…{digits[-4:]}" if len(digits) >= 4 else "…"


@dataclass(frozen=True)
class InboundCallResult:
    twiml: str
    registered: bool


class TwilioInboundService:
    def __init__(
        self,
        *,
        auth_token: str | None,
        public_base_url: str | None,
        agent_id: str | None,
        client: RegisterCallClient | None,
    ):
        self._auth_token = auth_token
        self._url = f"{public_base_url.rstrip('/')}{INBOUND_PATH}" if public_base_url else None
        self._agent_id = agent_id
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "TwilioInboundService":
        client = ElevenLabsRegisterCallClient(settings.elevenlabs_api_key) if settings.elevenlabs_api_key else None
        return cls(
            auth_token=settings.twilio_auth_token,
            public_base_url=settings.public_base_url,
            agent_id=settings.elevenlabs_agent_id,
            client=client,
        )

    def missing_configuration(self) -> list[str]:
        return [
            name
            for name, value in (
                ("TWILIO_AUTH_TOKEN", self._auth_token),
                ("PREAUTH_PUBLIC_BASE_URL", self._url),
                ("ELEVENLABS_API_KEY", self._client),
                ("PREAUTH_ELEVENLABS_AGENT_ID", self._agent_id),
            )
            if not value
        ]

    def handle(self, raw_body: bytes, signature: str | None, query_string: str = "") -> InboundCallResult:
        missing = self.missing_configuration()
        if missing:
            raise TwilioChannelNotConfiguredError(
                "Inbound Twilio calls are disabled until configured", details={"missing": missing}
            )

        params = parse_qsl(raw_body.decode(errors="replace"), keep_blank_values=True)
        # Twilio signs the exact URL it called, including any query string configured on the number.
        url = f"{self._url}?{query_string}" if query_string else self._url
        twilio_signature.verify(url, params, signature, self._auth_token)

        form: dict[str, Any] = dict(params)
        call_sid = form.get("CallSid") or None
        from_number, to_number = (form.get("From") or "").strip(), (form.get("To") or "").strip()
        logger.info(
            "twilio_inbound_call_received",
            extra={"call_sid": call_sid, "from_tail": _tail(from_number), "to_tail": _tail(to_number)},
        )
        if not from_number or not to_number:
            raise InboundCallInvalidError(
                "Inbound call is missing From or To",
                details={"missing": [n for n, v in (("From", from_number), ("To", to_number)) if not v]},
            )

        try:
            twiml = self._client.register_call(
                {
                    "agent_id": self._agent_id,
                    "from_number": from_number,
                    "to_number": to_number,
                    "direction": "inbound",
                }
            )
        except RegisterCallError as e:
            logger.error(
                "elevenlabs_register_call_failed",
                extra={"call_sid": call_sid, "upstream_status": e.status, "reason": str(e)[:200]},
            )
            return InboundCallResult(twiml=FALLBACK_TWIML, registered=False)
        logger.info("elevenlabs_register_call_succeeded", extra={"call_sid": call_sid})
        return InboundCallResult(twiml=twiml, registered=True)
