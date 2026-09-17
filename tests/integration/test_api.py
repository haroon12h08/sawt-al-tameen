"""HTTP surface: staff and portal endpoints, error envelope, tracing."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from preauth.api.app import create_app
from tests.integration.helpers import REVIEWER, approved_case, decide
from preauth.domain.enums import HumanDecisionType

AGENT_H = {"X-Actor-Type": "VOICE_AGENT", "X-Actor-Id": "voice-1"}
REVIEWER_H = {"X-Actor-Type": "HUMAN_REVIEWER", "X-Actor-Id": "rev-1", "X-Actor-Roles": "CLINICAL_REVIEWER"}
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


def test_case_endpoints_expose_the_catalogue_view(client, services):
    _, result = approved_case(services)
    case = client.get(f"/api/v1/cases/{result.case_id}", headers=AGENT_H).json()
    assert case["member"]["tier"]["tier_id"] == "EXECUTIVE"
    assert case["procedure"]["procedure_code"] == "SP-20040"
    assert case["estimated_cost_aed"] == 21000

    by_reference = client.get(f"/api/v1/cases/by-reference/{result.case_reference}", headers=AGENT_H)
    assert by_reference.json()["id"] == result.case_id

    status = client.get(f"/api/v1/cases/{result.case_id}/status", headers=AGENT_H).json()
    assert status["status"] == "PENDING_HUMAN_REVIEW" and status["final_decision"] is None

    recommendation = client.get(f"/api/v1/cases/{result.case_id}/recommendation", headers=AGENT_H).json()
    assert recommendation["advisory_only"] is True
    assert recommendation["ruleset_name"] == "uae-preauth-ruleset"
    assert any("Schedule of Benefits" in s["document"] for s in recommendation["sources"])


def test_review_flow_over_http(client, services):
    _, result = approved_case(services)
    queue = client.get("/api/v1/review/queues/CLINICAL_REVIEW", headers=REVIEWER_H).json()
    assert result.case_id in [item["case_id"] for item in queue]

    packet = client.get(f"/api/v1/review/cases/{result.case_id}", headers=REVIEWER_H).json()
    recommendation_id = packet["current_recommendation"]["id"]
    assert client.post(f"/api/v1/review/cases/{result.case_id}/assignment", headers=REVIEWER_H).status_code == 200

    decision = client.post(
        f"/api/v1/review/cases/{result.case_id}/decision",
        json={
            "recommendation_id": recommendation_id,
            "decision": "APPROVE",
            "rationale": "Criteria met; documentation complete.",
        },
        headers=REVIEWER_H,
    )
    assert decision.status_code == 201 and decision.json()["to_status"] == "APPROVED"


def test_document_registration_and_closure(client, services):
    _, result = approved_case(services)
    decide(services, result.case_id, REVIEWER, HumanDecisionType.DENY)
    closed = client.post(
        f"/api/v1/cases/{result.case_id}/closure",
        json={"reason": "DECISION_COMMUNICATED"},
        headers={"X-Actor-Type": "SYSTEM", "X-Actor-Id": "batch"},
    )
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"


def test_missing_actor_headers(client):
    _error(client.get("/api/v1/review/queues/CLINICAL_REVIEW"), 401, "ACTOR_REQUIRED")
    _error(
        client.get("/api/v1/review/queues/CLINICAL_REVIEW", headers={**AGENT_H, "X-Actor-Type": "ROBOT"}),
        401, "INVALID_ACTOR",
    )


def test_schema_violation_envelope(client, services):
    _, result = approved_case(services)
    body = _error(
        client.post(f"/api/v1/cases/{result.case_id}/documents", json={"document_type": "SELFIE"}, headers=AGENT_H),
        422, "REQUEST_VALIDATION_FAILED",
    )
    assert body["details"]["errors"]


def test_not_found_carries_case_id(client):
    missing = "00000000-0000-4000-8000-000000000000"
    body = _error(client.get(f"/api/v1/cases/{missing}", headers=AGENT_H), 404, "CASE_NOT_FOUND")
    assert body["case_id"] == missing


def test_audit_endpoint_is_reviewer_only(client, services):
    _, result = approved_case(services)
    _error(client.get(f"/api/v1/cases/{result.case_id}/audit-events", headers=AGENT_H), 403, "HUMAN_REVIEWER_REQUIRED")
    events = client.get(f"/api/v1/cases/{result.case_id}/audit-events", headers=REVIEWER_H).json()
    assert events[0]["event_type"] == "CASE_CREATED"


def test_unhandled_exception_is_logged_and_enveloped(client, services, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(services.queries, "get_status", boom)
    with caplog.at_level(logging.ERROR, logger="preauth.api.access"):
        body = _error(
            client.get("/api/v1/cases/00000000-0000-4000-8000-000000000001/status", headers=AGENT_H),
            500, "INTERNAL_ERROR",
        )
    assert "database exploded" not in json.dumps(body)
    assert any(r.exc_info for r in caplog.records)


def test_openapi_documents_every_operation(client):
    spec = client.get("/openapi.json").json()
    for path, operations in spec["paths"].items():
        for method, op in operations.items():
            assert op.get("summary"), (method, path)
            if path.startswith("/api/"):
                assert "**Authorisation:**" in op["description"], (method, path)
                assert "**State transitions:**" in op["description"], (method, path)
