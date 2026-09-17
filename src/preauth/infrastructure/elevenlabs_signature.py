"""Verification of ElevenLabs webhook signatures.

Header ``elevenlabs-signature: t=<unix seconds>,v0=<hex HMAC-SHA256 of "<t>.<raw body>">``, matching the official
SDK's ``construct_event``. Signatures older than the tolerance are rejected to limit replay.
"""

import hashlib
import hmac
import time

from preauth.domain.errors import DomainError

TOLERANCE_SECS = 30 * 60


class WebhookSignatureError(DomainError):
    code = "WEBHOOK_SIGNATURE_INVALID"


def sign(raw_body: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v0={digest}"


def verify(raw_body: bytes, header: str | None, secret: str, now: float | None = None) -> None:
    if not header:
        raise WebhookSignatureError("Missing elevenlabs-signature header")
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    timestamp, signature = parts.get("t"), parts.get("v0")
    if not timestamp or not signature or not timestamp.isdigit():
        raise WebhookSignatureError("Malformed elevenlabs-signature header")
    if int(timestamp) < (now if now is not None else time.time()) - TOLERANCE_SECS:
        raise WebhookSignatureError("Webhook signature timestamp is outside the tolerance window")
    expected = sign(raw_body, secret, int(timestamp)).split("v0=", 1)[1]
    if not hmac.compare_digest(expected, signature):
        raise WebhookSignatureError("Webhook signature does not match")
