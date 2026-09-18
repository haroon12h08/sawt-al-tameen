"""Client for ElevenLabs' Twilio register-call endpoint.

``POST /v1/convai/twilio/register-call`` registers a call arriving on our own Twilio number with an ElevenLabs agent
and returns the TwiML that connects Twilio's media stream to that agent. Standard library only: the backend has no
HTTP client dependency, and one short request per inbound call does not need one.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Protocol

API_BASE = "https://api.elevenlabs.io"
REGISTER_CALL_PATH = "/v1/convai/twilio/register-call"


class RegisterCallError(Exception):
    """ElevenLabs refused or could not be reached. Never carries the API key."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RegisterCallClient(Protocol):
    def register_call(self, payload: dict[str, Any]) -> str: ...


class ElevenLabsRegisterCallClient:
    def __init__(self, api_key: str, api_base: str = API_BASE, timeout_secs: float = 10.0):
        self._api_key = api_key
        self._url = api_base.rstrip("/") + REGISTER_CALL_PATH
        # Twilio waits 15 s for a webhook response; stay well inside that so the caller hears our fallback.
        self._timeout = timeout_secs

    def register_call(self, payload: dict[str, Any]) -> str:
        request = urllib.request.Request(
            self._url,
            method="POST",
            data=json.dumps(payload).encode(),
            headers={"xi-api-key": self._api_key, "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read().decode()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            raise RegisterCallError(f"ElevenLabs returned HTTP {e.code}: {detail}", status=e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RegisterCallError(f"ElevenLabs could not be reached: {getattr(e, 'reason', e)}") from None
        # The reference documents the body as TwiML; tolerate it arriving JSON-encoded as a string, as SDKs return it.
        if body.lstrip().startswith('"'):
            try:
                body = json.loads(body)
            except ValueError:
                pass
        if not isinstance(body, str) or "<Response" not in body:
            raise RegisterCallError("ElevenLabs responded without TwiML", status=200)
        return body
