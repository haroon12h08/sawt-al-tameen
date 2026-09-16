import json
import logging

import pytest
from fastapi.testclient import TestClient

from preauth.api.app import create_app
from tests.integration.helpers import SERVICE_DATE

AGENT_H = {"X-Actor-Type": "VOICE_AGENT", "X-Actor-Id": "voice-1"}
REVIEWER_H = {"X-Actor-Type": "HUMAN_REVIEWER", "X-Actor-Id": "rev-1", "X-Actor-Roles": "CLINICAL_REVIEWER"}
DIRECTOR_H = {"X-Actor-Type": "HUMAN_REVIEWER", "X-Actor-Id": "md-1", "X-Actor-Roles": "MEDICAL_DIRECTOR"}

INFO = {
    "provider_number": "PRV-100234",
    "patient": {"member_id": "MBR-5001-01", "date_of_birth": "1984-03-12"},
    "policy_number": "POL-000101",
    "procedure_code": "PROC-MRI-KNEE",
    "requested_service_date": SERVICE_DATE.isoformat(),
    "place_of_service": "OUTPATIENT",
    "diagnosis_code": "M23.221",
    "urgency": "STANDARD",
    "conservative_treatment_weeks": 8,
}
DOC = {
    "document_type": "CLINICAL_NOTES",
    "title": "Orthopaedic consult note",
    "storage_uri": "docstore://synthetic/notes-1.pdf",
    "media_type": "application/pdf",
}


@pytest.fixture
def client(services):
    with TestClient(create_app(services), raise_server_exceptions=False) as c:
        yield c


def _error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()["error"]
    assert body["code"] == code
    assert body["request_id"] == response.headers["X-Request-ID"]
    return body


def _create(client, info=INFO):
    r = client.post("/api/v1/cases", json={"information": info}, headers=AGENT_H)
    assert r.status_code == 201, r.text
    return r.json()


def test_end_to_end_http_flow_with_override(client):
    case = _create(client, {**INFO, "conservative_treatment_weeks": 2})
    cid = case["id"]
    assert case["status"] == "INFORMATION_COLLECTION"

    assert client.post(f"/api/v1/cases/{cid}/documents", json=DOC, headers=AGENT_H).status_code == 201
    required = client.get(f"/api/v1/cases/{cid}/required-information", headers=AGENT_H).json()
    assert required["intake_complete"] is True and required["missing_information"] == []

    evaluation = client.post(f"/api/v1/cases/{cid}/evaluation", headers=AGENT_H).json()
    assert evaluation["status"] == "RECOMMENDATION_READY"
    rec = evaluation["recommendation"]
    assert rec["outcome"] == "RECOMMEND_DENIAL" and rec["advisory_only"] is True

    routed = client.post(f"/api/v1/cases/{cid}/human-review-request", headers=AGENT_H).json()
    assert routed["status"] == "PENDING_HUMAN_REVIEW" and routed["review_queue"] == "CLINICAL_REVIEW"

    queue = client.get("/api/v1/review/queues/CLINICAL_REVIEW", headers=REVIEWER_H).json()
    assert [i["case_id"] for i in queue] == [cid]
    packet = client.get(f"/api/v1/review/cases/{cid}", headers=REVIEWER_H).json()
    assert packet["current_recommendation"]["id"] == rec["id"]

    assert client.post(f"/api/v1/review/cases/{cid}/assignment", headers=REVIEWER_H).status_code == 200
    decision = client.post(
        f"/api/v1/review/cases/{cid}/decision",
        json={"recommendation_id": rec["id"], "decision": "APPROVE", "rationale": "Red-flag symptoms documented."},
        headers=REVIEWER_H,
    )
    assert decision.status_code == 201, decision.text
    assert decision.json()["is_override"] is True

    status = client.get(f"/api/v1/cases/{cid}/status", headers=AGENT_H).json()
    assert status["status"] == "APPROVED" and status["final_decision"]["reviewer_id"] == "rev-1"
    assert client.get(f"/api/v1/cases/{cid}/recommendation", headers=AGENT_H).json()["outcome"] == "RECOMMEND_DENIAL"

    events = client.get(f"/api/v1/cases/{cid}/audit-events", headers=REVIEWER_H).json()
    assert "RECOMMENDATION_OVERRIDDEN" in [e["event_type"] for e in events]

    by_ref = client.get(f"/api/v1/cases/by-reference/{case['case_reference']}", headers=AGENT_H)
    assert by_ref.json()["id"] == cid


def test_missing_actor_headers(client):
    _error(client.post("/api/v1/cases", json={}), 401, "ACTOR_REQUIRED")
    _error(client.post("/api/v1/cases", json={}, headers={**AGENT_H, "X-Actor-Type": "ROBOT"}), 401, "INVALID_ACTOR")
    _error(
        client.post("/api/v1/cases", json={}, headers={**AGENT_H, "X-Actor-Roles": "MEDICAL_DIRECTOR"}),
        401,
        "INVALID_ACTOR",
    )


def test_schema_violation_envelope(client):
    body = _error(
        client.post("/api/v1/cases", json={"information": {"urgency": "ASAP", "extra": 1}}, headers=AGENT_H),
        422,
        "REQUEST_VALIDATION_FAILED",
    )
    assert len(body["details"]["errors"]) >= 2


def test_malformed_case_id_rejected(client):
    _error(client.get("/api/v1/cases/not-a-uuid", headers=AGENT_H), 422, "REQUEST_VALIDATION_FAILED")


def test_not_found_carries_case_id(client):
    missing = "00000000-0000-4000-8000-000000000000"
    body = _error(client.get(f"/api/v1/cases/{missing}", headers=AGENT_H), 404, "CASE_NOT_FOUND")
    assert body["case_id"] == missing


def test_domain_validation_error(client):
    _error(
        client.post("/api/v1/cases", json={"information": {"provider_number": "PRV-000000"}}, headers=AGENT_H),
        422,
        "UNKNOWN_PROVIDER",
    )


def test_invalid_transition_is_409(client):
    cid = client.post("/api/v1/cases", json={}, headers=AGENT_H).json()["id"]
    body = _error(client.post(f"/api/v1/cases/{cid}/evaluation", headers=AGENT_H), 409, "INVALID_STATE_TRANSITION")
    assert body["details"]["from_status"] == "RECEIVED"


def test_agent_cannot_record_decision_over_http(client):
    cid = _create(client)["id"]
    client.post(f"/api/v1/cases/{cid}/documents", json=DOC, headers=AGENT_H)
    rec = client.post(f"/api/v1/cases/{cid}/evaluation", headers=AGENT_H).json()["recommendation"]
    client.post(f"/api/v1/cases/{cid}/human-review-request", headers=AGENT_H)
    _error(
        client.post(
            f"/api/v1/review/cases/{cid}/decision",
            json={"recommendation_id": rec["id"], "decision": "APPROVE", "rationale": "Self-approval attempt."},
            headers=AGENT_H,
        ),
        403,
        "HUMAN_REVIEWER_REQUIRED",
    )
    _error(client.post(f"/api/v1/review/cases/{cid}/assignment", headers=DIRECTOR_H), 403, "INSUFFICIENT_REVIEWER_ROLE")


def test_request_id_propagates_and_is_audited(client):
    cid = client.post("/api/v1/cases", json={}, headers={**AGENT_H, "X-Request-ID": "trace-42"}).json()["id"]
    events = client.get(f"/api/v1/cases/{cid}/audit-events", headers=REVIEWER_H).json()
    assert events[0]["request_id"] == "trace-42"


def test_unhandled_exception_is_logged_and_enveloped(client, services, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(services.queries, "get_status", boom)
    cid = "00000000-0000-4000-8000-000000000001"
    with caplog.at_level(logging.ERROR, logger="preauth.api.access"):
        body = _error(client.get(f"/api/v1/cases/{cid}/status", headers=AGENT_H), 500, "INTERNAL_ERROR")
    assert "database exploded" not in json.dumps(body)
    assert any(r.exc_info for r in caplog.records)


def test_structured_log_lines_carry_case_and_request_ids(client, caplog):
    from preauth.infrastructure.observability import JsonFormatter

    cid = _create(client)["id"]
    with caplog.at_level(logging.INFO):
        client.get(f"/api/v1/cases/{cid}/status", headers={**AGENT_H, "X-Request-ID": "trace-99"})
    access = [r for r in caplog.records if r.getMessage() == "request_completed"][-1]
    line = json.loads(JsonFormatter().format(access))
    assert line["request_id"] == "trace-99" and line["case_id"] == cid and line["status_code"] == 200


def test_openapi_documents_every_operation(client):
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            assert op.get("summary"), (method, path)
            if path.startswith("/api/"):
                assert "**Authorisation:**" in op["description"], (method, path)
                assert "**State transitions:**" in op["description"], (method, path)
                assert "401" in op["responses"], (method, path)
