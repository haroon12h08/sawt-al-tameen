import pytest
from fastapi.testclient import TestClient

from preauth.agent_tools.toolbox import RECOMMENDATION_NOTICE, AgentToolbox, ToolArgumentsInvalidError
from preauth.api.app import create_app
from preauth.domain.errors import AuthorizationError, NotFoundError, ValidationFailedError
from tests.integration.helpers import AGENT, PORTAL, REVIEWER, SERVICE_DATE, document

EXPECTED_TOOLS = {
    "create_pre_authorization_case",
    "get_case",
    "get_required_information",
    "submit_information",
    "get_case_status",
    "evaluate_case",
    "get_recommendation",
    "request_human_review",
    "request_human_callback",
}


@pytest.fixture
def toolbox(services):
    return AgentToolbox(services)


def test_toolbox_exposes_exactly_the_agreed_tools(toolbox):
    assert set(toolbox.tool_names) == EXPECTED_TOOLS


def test_no_tool_can_decide_assign_or_read_audit(toolbox):
    forbidden = ("decision", "decide", "approve", "deny", "assign", "audit", "close", "override")
    for definition in toolbox.describe():
        assert not any(word in definition["name"] for word in forbidden)
        props = definition["input_schema"].get("properties", {})
        assert "decision" not in props and "rationale" not in props


def test_tool_definitions_have_schemas(toolbox):
    for definition in toolbox.describe():
        assert definition["description"]
        assert definition["input_schema"]["type"] == "object"
        assert definition["output_schema"]


def test_voice_conversation_flow_through_tools(toolbox, services):
    case = toolbox.invoke("create_pre_authorization_case", AGENT, {})
    cid = case["id"]
    assert toolbox.invoke("get_case", AGENT, {"case_reference": case["case_reference"].lower()})["id"] == cid

    needed = toolbox.invoke("get_required_information", AGENT, {"case_id": cid})
    assert needed["intake_complete"] is False

    toolbox.invoke(
        "submit_information",
        AGENT,
        {
            "case_id": cid,
            "caller_name": "Aisha",
            "caller_role": "PROVIDER_STAFF",
            "provider_number": "prv-100234",
            "patient_member_id": "MBR-5001-01",
            "patient_date_of_birth": "1984-03-12",
            "policy_number": "POL-000101",
            "procedure_code": "PROC-MRI-KNEE",
            "requested_service_date": SERVICE_DATE.isoformat(),
            "place_of_service": "OUTPATIENT",
            "diagnosis_code": "m23.221",
            "urgency": "STANDARD",
        },
    )
    needed = toolbox.invoke("get_required_information", AGENT, {"case_id": cid})
    assert {m["code"] for m in needed["missing_information"]} == {
        "document.CLINICAL_NOTES",
        "clinical.conservative_treatment_weeks",
    }
    assert all(m["source"] == "PROVIDER" for m in needed["missing_information"])

    # Documents arrive via the provider portal, not the voice channel.
    services.cases.register_document(cid, PORTAL, document())
    toolbox.invoke("submit_information", AGENT, {"case_id": cid, "conservative_treatment_weeks": 7})

    result = toolbox.invoke("evaluate_case", AGENT, {"case_id": cid})
    assert result["status"] == "RECOMMENDATION_READY"

    rec = toolbox.invoke("get_recommendation", AGENT, {"case_id": cid})
    assert rec["notice"] == RECOMMENDATION_NOTICE
    assert rec["recommendation"]["advisory_only"] is True
    assert {"document", "section", "rule_ids"} <= set(rec["recommendation"]["sources"][0])

    routed = toolbox.invoke("request_human_review", AGENT, {"case_id": cid})
    assert routed["status"] == "PENDING_HUMAN_REVIEW"
    status = toolbox.invoke("get_case_status", AGENT, {"case_id": cid})
    assert status["final_decision"] is None


def test_tools_require_voice_agent_actor(toolbox):
    with pytest.raises(AuthorizationError) as exc:
        toolbox.invoke("create_pre_authorization_case", REVIEWER, {})
    assert exc.value.code == "VOICE_AGENT_REQUIRED"


def test_tool_argument_validation(toolbox):
    with pytest.raises(ToolArgumentsInvalidError):
        toolbox.invoke("get_case", AGENT, {})
    with pytest.raises(ToolArgumentsInvalidError):
        toolbox.invoke("get_case_status", AGENT, {"case_id": "abc", "extra": True})
    with pytest.raises(NotFoundError) as exc:
        toolbox.invoke("approve_case", AGENT, {})
    assert exc.value.code == "TOOL_NOT_FOUND"


def test_domain_errors_pass_through_unchanged(toolbox):
    cid = toolbox.invoke("create_pre_authorization_case", AGENT, {})["id"]
    with pytest.raises(ValidationFailedError) as exc:
        toolbox.invoke("submit_information", AGENT, {"case_id": cid, "provider_number": "PRV-000001"})
    assert exc.value.code == "UNKNOWN_PROVIDER"


def test_http_tool_transport(services):
    headers = {"X-Actor-Type": "VOICE_AGENT", "X-Actor-Id": "voice-1"}
    with TestClient(create_app(services)) as client:
        tools = client.get("/api/v1/agent/tools", headers=headers).json()
        assert {t["name"] for t in tools} == EXPECTED_TOOLS
        created = client.post("/api/v1/agent/tools/create_pre_authorization_case", json={}, headers=headers)
        assert created.status_code == 200 and created.json()["status"] == "RECEIVED"
        bad = client.post("/api/v1/agent/tools/get_case", json={}, headers=headers)
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "TOOL_ARGUMENTS_INVALID"
        reviewer = client.post(
            "/api/v1/agent/tools/get_case",
            json={"case_id": created.json()["id"]},
            headers={"X-Actor-Type": "HUMAN_REVIEWER", "X-Actor-Id": "r", "X-Actor-Roles": "CLINICAL_REVIEWER"},
        )
        assert reviewer.status_code == 403
