import json
import time

import pytest
from fastapi.testclient import TestClient

from preauth.api.app import create_app
from preauth.domain.enums import AuditEventType, CaseStatus, HumanDecisionType, RecommendationOutcome
from preauth.infrastructure.elevenlabs_signature import sign
from preauth.infrastructure.settings import Settings
from tests.integration.helpers import PORTAL, REVIEWER, SERVICE_DATE, decide, document

TOKEN = "test-voice-token"
WEBHOOK_SECRET = "test-webhook-secret"
GATEWAY = "test-gateway-secret"
VOICE_H = {"Authorization": f"Bearer {TOKEN}", "X-Conversation-ID": "conv_test_001"}
REVIEWER_H = {
    "X-Actor-Type": "HUMAN_REVIEWER", "X-Actor-Id": "rev-1", "X-Actor-Roles": "CLINICAL_REVIEWER",
    "X-Gateway-Secret": GATEWAY,
}

FULL_INTAKE = {
    "caller_name": "Aisha Rahman",
    "caller_role": "PROVIDER_STAFF",
    "provider_number": "PRV-100234",
    "patient_member_id": "MBR-5001-01",
    "patient_date_of_birth": "1984-03-12",
    "policy_number": "POL-000101",
    "procedure_code": "PROC-MRI-KNEE",
    "requested_service_date": SERVICE_DATE.isoformat(),
    "place_of_service": "OUTPATIENT",
    "diagnosis_code": "M23.221",
    "urgency": "STANDARD",
    "conservative_treatment_weeks": 8,
    "clinical_summary": "",  # platforms send empty strings for unset parameters
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
                {"role": "agent", "message": "Pre-authorisation desk, how can I help?", "tool_calls": None},
                {"role": "user", "message": "I need an MRI approved.", "tool_calls": None},
            ],
            "metadata": {"call_duration_secs": 184},
            "analysis": {"transcript_summary": "Clinic requested knee MRI pre-auth.", "call_successful": "success"},
        },
    }
    raw = json.dumps(payload).encode()
    header = sign(raw, secret, timestamp if timestamp is not None else int(time.time()))
    return client.post(
        "/api/v1/voice/elevenlabs/post-call",
        content=raw,
        headers={"elevenlabs-signature": header, "content-type": "application/json"},
    )


def _voice_case_in_review(client, services):
    case = tool(client, "create_pre_authorization_case", FULL_INTAKE)["result"]
    services.cases.register_document(case["id"], PORTAL, document())
    evaluation = tool(client, "evaluate_case", {"case_id": case["id"]})["result"]
    assert evaluation["recommendation"]["outcome"] == "RECOMMEND_APPROVAL"
    assert tool(client, "request_human_review", {"case_id": case["id"]})["result"]["status"] == "PENDING_HUMAN_REVIEW"
    return case["id"]


# --------------------------------------------------------------------------- tool transport


def test_voice_tools_require_configuration_and_token(services):
    with TestClient(create_app(services, Settings()), raise_server_exceptions=False) as unconfigured:
        r = unconfigured.post("/api/v1/voice/tools/get_case_status", json={}, headers=VOICE_H)
        assert r.status_code == 503 and r.json()["error"]["code"] == "CHANNEL_NOT_CONFIGURED"
    with TestClient(create_app(services, Settings(voice_agent_token=TOKEN))) as configured:
        r = configured.post(
            "/api/v1/voice/tools/get_case_status", json={}, headers={"Authorization": "Bearer wrong"}
        )
        assert r.status_code == 401 and r.json()["error"]["code"] == "VOICE_TOKEN_INVALID"


def test_voice_tool_success_normalises_platform_values(client):
    body = tool(client, "create_pre_authorization_case", FULL_INTAKE)
    assert body["ok"] is True
    case = body["result"]
    assert case["status"] == "INFORMATION_COLLECTION"
    assert case["caller_role"] == "PROVIDER_STAFF" and case["clinical_summary"] is None


def test_voice_tool_business_error_is_actionable_not_http_failure(client):
    body = tool(client, "create_pre_authorization_case", {**FULL_INTAKE, "patient_date_of_birth": "1984-03-13"})
    assert body["ok"] is False
    assert body["error"]["code"] == "MEMBER_NOT_VERIFIED"
    assert "confirm" in body["guidance"]

    body = tool(client, "create_pre_authorization_case", {"patient_member_id": "MBR-5001-01"})
    assert body["ok"] is False and body["error"]["code"] == "TOOL_ARGUMENTS_INVALID"


def test_supplier_cannot_open_preauth_case_but_can_request_callback(client, services):
    body = tool(client, "create_pre_authorization_case", {"caller_role": "SUPPLIER", "caller_name": "Omar"})
    assert body["ok"] is False and body["error"]["code"] == "CALLER_NOT_ELIGIBLE"

    callback = tool(
        client,
        "request_human_callback",
        {
            "caller_name": "Omar Haddad",
            "caller_organisation": "Gulf Medical Supplies",
            "caller_role": "SUPPLIER",
            "callback_phone": "+971501234567",
            "preferred_language": "AR",
            "reason": "SUPPLIER_ENQUIRY",
            "summary": "Wants to know the onboarding requirements for durable medical equipment suppliers.",
        },
    )
    assert callback["ok"] is True
    result = callback["result"]
    assert result["reference"].startswith("CB-") and result["conversation_id"] == "conv_test_001"
    assert result["preferred_language"] == "ar" and result["status"] == "OPEN"

    listed = client.get("/api/v1/review/callbacks", headers=REVIEWER_H).json()
    assert [c["id"] for c in listed] == [result["id"]]
    resolved = client.post(
        f"/api/v1/review/callbacks/{result['id']}/resolution",
        json={"resolution_note": "Called back and emailed onboarding checklist."},
        headers=REVIEWER_H,
    )
    assert resolved.status_code == 200 and resolved.json()["status"] == "RESOLVED"
    assert client.get("/api/v1/review/callbacks", headers=REVIEWER_H).json() == []


def test_case_linked_callback_is_audited(client, services):
    case = tool(client, "create_pre_authorization_case", FULL_INTAKE)["result"]
    tool(
        client,
        "request_human_callback",
        {
            "case_id": case["id"], "caller_name": "Aisha Rahman", "caller_role": "PROVIDER_STAFF",
            "callback_phone": "+971501112233", "preferred_language": "en", "reason": "NON_STANDARD_REQUEST",
            "summary": "Asks whether a staged bilateral procedure can be authorised as one request.",
        },
    )
    types = [e.event_type for e in services.queries.get_audit_history(case["id"], REVIEWER)]
    assert AuditEventType.HUMAN_CALLBACK_REQUESTED in types


def test_no_voice_tool_can_decide(client):
    for name in ("record_decision", "approve_case", "deny_case", "assign_reviewer"):
        body = tool(client, name, {})
        assert body["ok"] is False and body["error"]["code"] == "TOOL_NOT_FOUND"


# --------------------------------------------------------------------------- transcript before sign-off


def test_human_sign_off_requires_call_transcript(client, services):
    case_id = _voice_case_in_review(client, services)

    services.review.assign_reviewer(case_id, REVIEWER)
    packet = services.review.review_packet(case_id, REVIEWER)
    assert packet.pending_call_conversation_ids == ["conv_test_001"] and packet.calls == []
    with pytest.raises(Exception) as exc:
        decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE, assign=False)
    assert exc.value.code == "CALL_RECORD_PENDING"

    response = post_call(client)
    assert response.status_code == 200, response.text
    assert response.json()["linked_case_ids"] == [case_id]

    packet = services.review.review_packet(case_id, REVIEWER)
    assert packet.pending_call_conversation_ids == []
    assert packet.calls[0].transcript_summary == "Clinic requested knee MRI pre-auth."
    assert packet.calls[0].call_duration_secs == 184
    assert AuditEventType.CALL_RECORDED in [e.event_type for e in packet.audit_history]

    decision = decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE, assign=False)
    assert decision.to_status is CaseStatus.APPROVED


def test_non_voice_cases_are_not_blocked(services):
    from tests.integration.helpers import case_in_review

    case_id, _ = case_in_review(services)
    assert decide(services, case_id, REVIEWER, HumanDecisionType.APPROVE).to_status is CaseStatus.APPROVED


# --------------------------------------------------------------------------- post-call webhook


def test_post_call_webhook_rejects_bad_signatures(client):
    assert post_call(client, secret="wrong").status_code == 401
    stale = post_call(client, timestamp=int(time.time()) - 3600)
    assert stale.status_code == 401 and stale.json()["error"]["code"] == "WEBHOOK_SIGNATURE_INVALID"
    unsigned = client.post("/api/v1/voice/elevenlabs/post-call", content=b"{}")
    assert unsigned.status_code == 401


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
        r = c.post("/api/v1/voice/elevenlabs/post-call", content=b"{}")
        assert r.status_code == 503


# --------------------------------------------------------------------------- gateway secret


def test_gateway_secret_protects_staff_api_but_not_voice_or_health(client):
    no_secret = {k: v for k, v in REVIEWER_H.items() if k != "X-Gateway-Secret"}
    r = client.get("/api/v1/review/queues/CLINICAL_REVIEW", headers=no_secret)
    assert r.status_code == 401 and r.json()["error"]["code"] == "GATEWAY_SECRET_INVALID"
    assert client.get("/api/v1/review/queues/CLINICAL_REVIEW", headers=REVIEWER_H).status_code == 200
    assert client.get("/health").status_code == 200
    assert tool(client, "get_case", {"case_reference": "PA-00000000"})["error"]["code"] == "CASE_NOT_FOUND"


# --------------------------------------------------------------------------- source attribution


def test_recommendation_cites_policy_sources(client, services):
    case = tool(client, "create_pre_authorization_case", FULL_INTAKE)["result"]
    services.cases.register_document(case["id"], PORTAL, document())
    rec = tool(client, "evaluate_case", {"case_id": case["id"]})["result"]["recommendation"]
    documents = {(s["document"], s["section"]) for s in rec["sources"]}
    assert (
        "Gold PPO (synthetic) Schedule of Benefits and Pre-Authorisation Rules 2026",
        "Section 4.1 PROC-MRI-KNEE: MRI of knee without contrast",
    ) in documents
    assert ("Membership and policy register", "Policy POL-000101") in documents
    by_rule = {r["rule_id"]: r["sources"] for r in rec["rule_results"]}
    assert by_rule["DOC-001-REQUIRED-DOCUMENTS"][0]["section"].startswith("Section 4.1")


def test_knowledge_gap_has_no_fabricated_coverage_source(client):
    case = tool(
        client,
        "create_pre_authorization_case",
        {**FULL_INTAKE, "procedure_code": "PROC-GENETIC-PANEL", "diagnosis_code": "Z80.3"},
    )["result"]
    rec = tool(client, "evaluate_case", {"case_id": case["id"]})["result"]["recommendation"]
    assert rec["outcome"] == RecommendationOutcome.ESCALATE
    assert not any("Schedule of Benefits" in s["document"] for s in rec["sources"])
