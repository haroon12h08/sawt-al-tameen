"""Verification of Twilio webhook signatures.

Header ``X-Twilio-Signature``: base64 of HMAC-SHA1, keyed with the account's auth token, over the full URL Twilio
called followed by every POST parameter as ``name`` + ``value``, sorted by name. This is Twilio's documented
algorithm (``RequestValidator`` in their SDKs), implemented here so the project needs no Twilio dependency.

The URL must be the public one Twilio was configured with, not the address the request arrived on behind a tunnel,
which is why callers pass a canonical URL rather than the request's own.
"""

import base64
import hashlib
import hmac
from collections.abc import Iterable

from preauth.domain.errors import DomainError


class TwilioSignatureError(DomainError):
    code = "TWILIO_SIGNATURE_INVALID"


def sign(url: str, params: Iterable[tuple[str, str]], auth_token: str) -> str:
    payload = url + "".join(f"{name}{value}" for name, value in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify(url: str, params: Iterable[tuple[str, str]], signature: str | None, auth_token: str) -> None:
    if not signature:
        raise TwilioSignatureError("Missing X-Twilio-Signature header")
    if not hmac.compare_digest(sign(url, list(params), auth_token), signature):
        raise TwilioSignatureError("Twilio signature does not match")
