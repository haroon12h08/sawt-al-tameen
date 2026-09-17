"""The tool boundary: exactly three tools, none of which can decide anything."""

import pytest
from fastapi.testclient import TestClient

from preauth.agent_tools.toolbox import FORBIDDEN_TOOL_CONCEPTS, AgentToolbox, ToolArgumentsInvalidError
from preauth.api.app import create_app
from preauth.domain.errors import AuthorizationError, NotFoundError
from tests.integration.helpers import AGENT, REVIEWER, TREATMENT_DATE

EXPECTED_TOOLS = {"verify_caller", "check_coverage_rule", "log_transcript"}


@pytest.fixture
def toolbox(services):
    return AgentToolbox(services)


def test_exactly_three_tools_exist(toolbox):
    assert set(toolbox.tool_names) == EXPECTED_TOOLS


def test_no_tool_can_approve_deny_or_finalise(toolbox):
    """The absence of a decision tool is architectural: there is nothing to call."""
    for definition in toolbox.describe():
        assert not any(word in definition["name"] for word in FORBIDDEN_TOOL_CONCEPTS)
        properties = definition["input_schema"].get("properties", {})
        assert "decision" not in properties and "rationale" not in properties
    for name in ("record_decision", "approve_case", "deny_case", "assign_reviewer", "finalise_authorisation"):
        with pytest.raises(NotFoundError) as exc:
            toolbox.invoke(name, AGENT, {})
        assert exc.value.code == "TOOL_NOT_FOUND"


def test_tool_definitions_document_every_field(toolbox):
    for definition in toolbox.describe():
        assert definition["description"]
        for name, prop in definition["input_schema"]["properties"].items():
            assert prop.get("description"), (definition["name"], name)


def test_tools_require_a_voice_agent_actor(toolbox):
    with pytest.raises(AuthorizationError) as exc:
        toolbox.invoke("verify_caller", REVIEWER, {})
    assert exc.value.code == "VOICE_AGENT_REQUIRED"


def test_call_flow_through_the_toolbox(toolbox, services):
    verification = toolbox.invoke(
        "verify_caller",
        AGENT,
        {
            "caller_role": "PROVIDER_STAFF",
            "organisation_name": "Al Hudaiba Crescent Hospital",
            "caller_reference": "prv-30011",
            "caller_name": "Aisha Rahman",
            "member_policy_number": "pol-sa-2026-100001",
            "member_date_of_birth": "1986-04-17",
        },
    )
    assert verification["authorised"] is True
    assert verification["member"]["tier"]["tier_id"] == "EXECUTIVE"

    coverage = toolbox.invoke(
        "check_coverage_rule",
        AGENT,
        {
            "verification_id": verification["verification_id"],
            "procedure_code": "sp-20040",
            "treatment_date": TREATMENT_DATE,
            "estimated_cost_aed": 21000,
        },
    )
    assert coverage["advisory_only"] is True
    assert coverage["outcome"] == "REQUEST_MORE_INFORMATION"
    assert coverage["case_reference"].startswith("PA-")

    logged = toolbox.invoke(
        "log_transcript",
        AGENT,
        {
            "summary": "Arthroscopy requested; documents outstanding, caller informed.",
            "outcome_communicated": "MORE_INFORMATION_REQUESTED",
            "case_reference": coverage["case_reference"],
            "verification_id": verification["verification_id"],
        },
    )
    assert logged["reference"].startswith("CL-")


def test_coverage_check_cannot_run_before_verification(toolbox):
    with pytest.raises(ToolArgumentsInvalidError):
        toolbox.invoke(
            "check_coverage_rule",
            AGENT,
            {"procedure_code": "SP-20040", "treatment_date": TREATMENT_DATE, "estimated_cost_aed": 100},
        )


def test_tool_argument_validation(toolbox):
    with pytest.raises(ToolArgumentsInvalidError):
        toolbox.invoke("verify_caller", AGENT, {"caller_role": "PROVIDER_STAFF"})
    with pytest.raises(ToolArgumentsInvalidError):
        toolbox.invoke("log_transcript", AGENT, {"summary": "too short", "outcome_communicated": "NOPE"})


def test_http_tool_transport(services):
    headers = {"X-Actor-Type": "VOICE_AGENT", "X-Actor-Id": "voice-1"}
    with TestClient(create_app(services)) as client:
        tools = client.get("/api/v1/agent/tools", headers=headers).json()
        assert {t["name"] for t in tools} == EXPECTED_TOOLS
        response = client.post(
            "/api/v1/agent/tools/verify_caller",
            json={
                "caller_role": "PROVIDER_STAFF",
                "organisation_name": "Al Hudaiba Crescent Hospital",
                "caller_reference": "PRV-30011",
            },
            headers=headers,
        )
        assert response.status_code == 200 and response.json()["authorised"] is True
        forbidden = client.post("/api/v1/agent/tools/record_decision", json={}, headers=headers)
        assert forbidden.status_code == 404
