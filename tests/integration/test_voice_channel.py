"""The ElevenLabs transport: authentication, conversation linkage, transcripts and the gateway secret."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from preauth.api.app import create_app
from preauth.domain.enums import AuditEventType, CaseStatus, DocumentType, HumanDecisionType
from preauth.infrastructure.elevenlabs_signature import sign
from preauth.infrastructure.settings import Settings
from tests.integration.helpers import PORTAL, REVIEWER, TREATMENT_DATE, add_documents, decide

TOKEN = "test-voice-token"
WEBHOOK_SECRET = "test-webhook-secret"
GATEWAY = "test-gateway-secret"
VOICE_H = {"Authorization": f"Bearer {TOKEN}", "X-Conversation-ID": "conv_test_001"}
REVIEWER_H = {
    "X-Actor-Type": "HUMAN_REVIEWER", "X-Actor-Id": "rev-1", "X-Actor-Roles": "CLINICAL_REVIEWER",
    "X-Gateway-Secret": GATEWAY,
}
VERIFY_ARGS = {
    "caller_role": "PROVIDER_STAFF",
    "organisation_name": "Al Hudaiba Crescent Hospital",
    "caller_reference": "prv-30011",
    "caller_name": "Aisha Rahman",
    "member_policy_number": "POL-SA-2026-100001",
    "member_date_of_birth": "1986-04-17",
}


@pytest.fixture
def settings():
    return Settings(voice_agent_token=TOKEN, elevenlabs_webhook_secret=WEBHOOK_SECRET, gateway_secret=GATEWAY)


@pytest.fixture
def client(services, settings):
    with TestClient(create_app(services, settings), raise_server_exceptions=False) as c:
        yield c


def tool(client, name, body=None, headers=VOICE_H):
    response = client.post(f"/api/v1/voice/tools/{name}", json=body or {}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def post_call(client, conversation_id="conv_test_001", *, secret=WEBHOOK_SECRET, timestamp=None, event_type=None):
    payload = {
        "type": event_type or "post_call_transcription",
        "event_timestamp": int(time.time()),
        "data": {
            "agent_id": "agent_test",
            "conversation_id": conversation_id,
            "status": "done",
            "transcript": [
                {"role": "agent", "message": "Pre-authorisation desk. This is an AI assistant.", "tool_calls": None},
                {"role": "user", "message": "I need approval for a knee arthroscopy.", "tool_calls": None},
            ],
            "metadata": {"call_duration_secs": 212},
            "analysis": {"transcript_summary": "Clinic requested arthroscopy pre-auth.", "call_successful": "success"},
        },
    }
    raw = json.dumps(payload).encode()
    header = sign(raw, secret, timestamp if timestamp is not None else int(time.time()))
    return client.post(
        "/api/v1/voice/elevenlabs/post-call",
        content=raw,
        headers={"elevenlabs-signature": header, "content-type": "application/json"},
    )


def _voice_case_ready_for_review(client, services):
    verification = tool(client, "verify_caller", VERIFY_ARGS)["result"]
    first = tool(client, "check_coverage_rule", {
        "verification_id": verification["verification_id"], "procedure_code": "SP-20040",
        "treatment_date": TREATMENT_DATE, "estimated_cost_aed": 21000,
    })["result"]
    add_documents(services, first["case_id"], [
        DocumentType.CLINICAL_NOTES, DocumentType.OPERATIVE_PLAN, DocumentType.PRIOR_TREATMENT_RECORD
    ])
    ready = tool(client, "check_coverage_rule", {
        "verification_id": verification["verification_id"], "procedure_code": "SP-20040",
        "treatment_date": TREATMENT_DATE, "estimated_cost_aed": 21000, "case_reference": first["case_reference"],
    })["result"]
    assert ready["outcome"] == "RECOMMEND_APPROVAL"
    return ready["case_id"]


# --------------------------------------------------------------------------- transport


def test_voice_tools_require_configuration_and_token(services):
    with TestClient(create_app(services, Settings()), raise_server_exceptions=False) as unconfigured:
        r = unconfigured.post("/api/v1/voice/tools/verify_caller", json={}, headers=VOICE_H)
        assert r.status_code == 503 and r.json()["error"]["code"] == "CHANNEL_NOT_CONFIGURED"
    with TestClient(create_app(services, Settings(voice_agent_token=TOKEN))) as configured:
        r = configured.post("/api/v1/voice/tools/verify_caller", json={}, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401 and r.json()["error"]["code"] == "VOICE_TOKEN_INVALID"


def test_business_errors_are_actionable_not_http_failures(client):
    body = tool(client, "verify_caller", {**VERIFY_ARGS, "member_date_of_birth": "1990-01-01"})
    assert body["ok"] is True and body["result"]["authorised"] is False
    assert body["result"]["failure_code"] == "MEMBER_NOT_VERIFIED"

    invalid = tool(client, "check_coverage_rule", {"verification_id": "not-a-uuid"})
    assert invalid["ok"] is False and invalid["error"]["code"] == "TOOL_ARGUMENTS_INVALID"
    assert invalid["guidance"]


def test_no_voice_tool_can_decide(client):
    for name in ("record_decision", "approve_case", "deny_case", "assign_reviewer"):
        body = tool(client, name, {})
        assert body["ok"] is False and body["error"]["code"] == "TOOL_NOT_FOUND"


# --------------------------------------------------------------------------- transcript before sign-off


def test_human_sign_off_requires_the_call_transcript(client, services):
    case_id = _voice_case_ready_for_review(client, services)

    services.review.assign_reviewer(case_id, REVIEWER)
    packet = services.review.review_packet(case_id, REVIEWER)
    assert packet.pending_call_conversation_ids == ["conv_test_001"] and packet.calls == []
    with pytest.raises(Exception) as exc:
        decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE, assign=False)
    assert exc.value.code == "CALL_RECORD_PENDING"

    response = post_call(client)
    assert response.status_code == 200 and response.json()["linked_case_ids"] == [case_id]

    packet = services.review.review_packet(case_id, REVIEWER)
    assert packet.pending_call_conversation_ids == []
    assert packet.calls[0].transcript_summary == "Clinic requested arthroscopy pre-auth."
    assert AuditEventType.CALL_RECORDED in [e.event_type for e in packet.audit_history]

    decision = decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE, assign=False)
    assert decision.to_status is CaseStatus.APPROVED


def test_non_voice_cases_are_not_blocked(services):
    from tests.integration.helpers import approved_case

    _, result = approved_case(services)
    assert decide(services, result.case_id, REVIEWER, HumanDecisionType.APPROVE).to_status is CaseStatus.APPROVED


# --------------------------------------------------------------------------- post-call webhook


def test_post_call_webhook_rejects_bad_signatures(client):
    assert post_call(client, secret="wrong").status_code == 401
    stale = post_call(client, timestamp=int(time.time()) - 3600)
    assert stale.status_code == 401 and stale.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"
    assert client.post("/api/v1/voice/elevenlabs/post-call", content=b"{}").status_code == 401


def test_post_call_webhook_is_idempotent_and_ignores_other_events(client):
    first = post_call(client, "conv_idem")
    second = post_call(client, "conv_idem")
    assert first.json()["call_record_id"] == second.json()["call_record_id"]
    assert second.json()["detail"] == "Already recorded"
    ignored = post_call(client, "conv_audio", event_type="post_call_audio")
    assert ignored.status_code == 200 and ignored.json()["accepted"] is False


def test_post_call_webhook_malformed_body(client):
    raw = b"not json"
    r = client.post(
        "/api/v1/voice/elevenlabs/post-call",
        content=raw,
        headers={"elevenlabs-signature": sign(raw, WEBHOOK_SECRET, int(time.time()))},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "WEBHOOK_PAYLOAD_INVALID"


def test_post_call_webhook_disabled_without_secret(services):
    with TestClient(create_app(services, Settings()), raise_server_exceptions=False) as c:
        assert c.post("/api/v1/voice/elevenlabs/post-call", content=b"{}").status_code == 503


# --------------------------------------------------------------------------- gateway secret


def test_gateway_secret_protects_staff_api_but_not_voice_or_health(client):
    no_secret = {k: v for k, v in REVIEWER_H.items() if k != "X-Gateway-Secret"}
    r = client.get("/api/v1/review/queues/CLINICAL_REVIEW", headers=no_secret)
    assert r.status_code == 401 and r.json()["error"]["code"] == "GATEWAY_SECRET_INVALID"
    assert client.get("/api/v1/review/queues/CLINICAL_REVIEW", headers=REVIEWER_H).status_code == 200
    assert client.get("/health").status_code == 200
    assert tool(client, "verify_caller", VERIFY_ARGS)["ok"] is True
