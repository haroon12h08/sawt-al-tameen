import time

import pytest

from preauth.agent_tools.elevenlabs import all_webhook_tool_configs, request_body_schema
from preauth.agent_tools.toolbox import TOOLS
from preauth.infrastructure.elevenlabs_signature import WebhookSignatureError, sign, verify

ALLOWED_TYPES = {"string", "integer", "double", "boolean"}


def test_every_tool_converts_to_a_valid_elevenlabs_schema():
    configs = all_webhook_tool_configs(base_url="https://preauth.example.org/", authorization_secret_id="sec_1")
    assert [c["name"] for c in configs] == [t.name for t in TOOLS]
    for config in configs:
        api = config["api_schema"]
        assert config["type"] == "webhook" and config["description"]
        assert api["url"] == f"https://preauth.example.org/api/v1/voice/tools/{config['name']}"
        assert api["method"] == "POST"
        assert api["request_headers"]["Authorization"] == {"secret_id": "sec_1"}
        assert api["request_headers"]["X-Conversation-ID"] == {"variable_name": "system__conversation_id"}
        body = api["request_body_schema"]
        assert body["type"] == "object"
        for name, prop in body["properties"].items():
            assert prop["type"] in ALLOWED_TYPES, (config["name"], name)
            assert prop["description"], (config["name"], name)
        assert set(body["required"]) <= set(body["properties"])


def test_nested_models_are_refused():
    from pydantic import BaseModel, Field

    class Inner(BaseModel):
        x: int = Field(description="x")

    class Outer(BaseModel):
        inner: Inner = Field(description="nested")

    with pytest.raises(ValueError):
        request_body_schema(Outer)


def test_undocumented_properties_are_refused():
    from pydantic import BaseModel

    class Bare(BaseModel):
        value: str

    with pytest.raises(ValueError, match="description"):
        request_body_schema(Bare)


def test_signature_round_trip():
    raw = b'{"type":"post_call_transcription"}'
    now = int(time.time())
    verify(raw, sign(raw, "s3cret", now), "s3cret")
    with pytest.raises(WebhookSignatureError):
        verify(raw + b" ", sign(raw, "s3cret", now), "s3cret")
    with pytest.raises(WebhookSignatureError):
        verify(raw, sign(raw, "s3cret", now - 31 * 60), "s3cret")
    with pytest.raises(WebhookSignatureError):
        verify(raw, "v0=abc", "s3cret")


def test_signature_format_matches_official_sdk():
    # Reference: elevenlabs-python construct_event hashes f"{timestamp}.{body}" with HMAC-SHA256, hex digest.
    import hashlib
    import hmac

    raw, ts = b"{}", 1700000000
    expected = hmac.new(b"k", b"1700000000.{}", hashlib.sha256).hexdigest()
    assert sign(raw, "k", ts) == f"t={ts},v0={expected}"
