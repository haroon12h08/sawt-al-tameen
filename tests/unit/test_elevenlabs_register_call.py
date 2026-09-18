"""The register-call HTTP client, against a local stand-in for the ElevenLabs endpoint."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from preauth.infrastructure.elevenlabs_register_call import (
    REGISTER_CALL_PATH,
    ElevenLabsRegisterCallClient,
    RegisterCallError,
)

TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response><Connect><Stream url="wss://x"/></Connect></Response>'


@pytest.fixture
def upstream():
    seen: dict = {}
    reply = {"status": 200, "body": TWIML, "type": "application/xml"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.update(path=self.path, key=self.headers.get("xi-api-key"),
                        body=json.loads(self.rfile.read(int(self.headers["content-length"]))))
            raw = reply["body"].encode()
            self.send_response(reply["status"])
            self.send_header("content-type", reply["type"])
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", seen, reply
    server.shutdown()


def test_posts_the_payload_with_the_api_key_and_returns_twiml(upstream):
    base, seen, _ = upstream
    payload = {"agent_id": "agent_1", "from_number": "+1", "to_number": "+2", "direction": "inbound"}
    assert ElevenLabsRegisterCallClient("sk-test", api_base=base).register_call(payload) == TWIML
    assert seen == {"path": REGISTER_CALL_PATH, "key": "sk-test", "body": payload}


def test_twiml_sent_as_a_json_string_is_unwrapped(upstream):
    base, _, reply = upstream
    reply.update(body=json.dumps(TWIML), type="application/json")
    assert ElevenLabsRegisterCallClient("sk-test", api_base=base).register_call({}) == TWIML


@pytest.mark.parametrize("status,body", [(401, '{"detail":"invalid_api_key"}'), (500, "boom"), (200, "{}")])
def test_failures_raise_without_leaking_the_key(upstream, status, body):
    base, _, reply = upstream
    reply.update(status=status, body=body, type="application/json")
    with pytest.raises(RegisterCallError) as raised:
        ElevenLabsRegisterCallClient("sk-very-secret", api_base=base).register_call({})
    assert raised.value.status == status
    assert "sk-very-secret" not in str(raised.value)


def test_an_unreachable_api_raises_rather_than_hanging():
    client = ElevenLabsRegisterCallClient("sk-test", api_base="http://127.0.0.1:1", timeout_secs=2)
    with pytest.raises(RegisterCallError, match="could not be reached"):
        client.register_call({})
