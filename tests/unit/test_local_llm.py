"""The Ollama adapter: request shape, reply parsing, and how failures are reported."""

import httpx
import pytest

from preauth.local.config import LocalSettings
from preauth.local.errors import LocalServiceUnavailableError
from preauth.local.llm import OllamaChatModel, build_chat_model
from preauth.local.tools import flatten_input_schema, llm_tool_definitions
from preauth.agent_tools.toolbox import TOOLS

SETTINGS = LocalSettings(llm_model="qwen-test:7b", llm_base_url="http://localhost:11434")


def model_with(handler) -> OllamaChatModel:
    model = OllamaChatModel(SETTINGS)
    model._client = httpx.Client(transport=httpx.MockTransport(handler), base_url=SETTINGS.llm_base_url)
    return model


def test_tool_calls_are_parsed_from_the_reply():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "<think>which tool?</think>Let me check that.",
                    "tool_calls": [
                        {"function": {"name": "verify_caller", "arguments": {"caller_reference": "PRV-30011"}}}
                    ],
                }
            },
        )

    reply = model_with(handler).chat([{"role": "user", "content": "hi"}], [])
    # The model's private reasoning must never reach the caller.
    assert reply.text == "Let me check that."
    assert [(c.name, c.arguments) for c in reply.tool_calls] == [
        ("verify_caller", {"caller_reference": "PRV-30011"})
    ]


def test_arguments_sent_as_a_json_string_are_still_parsed():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "log_transcript", "arguments": '{"summary":"done"}'}}],
                }
            },
        )

    reply = model_with(handler).chat([], [])
    assert reply.tool_calls[0].arguments == {"summary": "done"}


def test_unparseable_arguments_become_empty_rather_than_invented():
    """A malformed tool call must reach the toolbox and be rejected there, not be guessed at here."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": "", "tool_calls": [{"function": {"name": "verify_caller", "arguments": "{not json"}}]}},
        )

    assert model_with(handler).chat([], []).tool_calls[0].arguments == {}


def test_the_request_carries_the_model_tools_and_temperature():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    tools = llm_tool_definitions(
        [{"name": t.name, "description": t.description, "input_schema": t.input_model.model_json_schema()} for t in TOOLS]
    )
    model_with(handler).chat([{"role": "user", "content": "hi"}], tools)
    assert captured["model"] == "qwen-test:7b"
    assert captured["stream"] is False
    assert captured["options"]["temperature"] == SETTINGS.llm_temperature
    assert [t["function"]["name"] for t in captured["tools"]] == ["verify_caller", "check_coverage_rule", "log_transcript"]


def test_a_stopped_ollama_says_how_to_start_it():
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(LocalServiceUnavailableError) as raised:
        model_with(handler).check()
    assert raised.value.details["start_it"] == "ollama serve"


def test_a_missing_model_says_how_to_pull_it():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})

    with pytest.raises(LocalServiceUnavailableError) as raised:
        model_with(handler).check()
    assert raised.value.details["pull_it"] == "ollama pull qwen-test:7b"


def test_a_present_model_passes_whatever_tag_it_carries():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "qwen-test:latest"}]})

    model_with(handler).check()  # does not raise


def test_a_timeout_names_the_setting_that_raises_it():
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(LocalServiceUnavailableError) as raised:
        model_with(handler).chat([], [])
    assert raised.value.details["raise_it"] == "PREAUTH_LOCAL_LLM_TIMEOUT_SECONDS"


def test_the_default_provider_is_ollama():
    assert build_chat_model(SETTINGS).name == "ollama:qwen-test:7b"


# --------------------------------------------------------------------------- tool schemas


def test_tool_definitions_come_from_the_toolbox_and_name_only_its_tools():
    described = [
        {"name": t.name, "description": t.description, "input_schema": t.input_model.model_json_schema()}
        for t in TOOLS
    ]
    definitions = llm_tool_definitions(described)
    assert [d["function"]["name"] for d in definitions] == [t.name for t in TOOLS]
    assert len(definitions) == 3


def test_schemas_offered_to_the_model_are_self_contained():
    """Small local models handle $ref and null-unions badly; every tool schema is flattened before it is sent."""
    import json

    for tool in TOOLS:
        flattened = flatten_input_schema(tool.input_model.model_json_schema())
        text = json.dumps(flattened)
        assert "$ref" not in text and "$defs" not in text and "anyOf" not in text, tool.name
        for name, prop in flattened["properties"].items():
            assert prop.get("description"), (tool.name, name)
        assert set(flattened["required"]) <= set(flattened["properties"])


def test_enums_are_listed_inline_so_the_model_can_see_the_allowed_values():
    flattened = flatten_input_schema(TOOLS[0].input_model.model_json_schema())
    assert flattened["properties"]["caller_role"]["enum"] == ["PROVIDER_STAFF", "BROKER", "SUPPLIER", "OTHER"]


def test_a_tool_call_printed_as_json_is_recovered():
    """Not every model packaged for Ollama uses the native tool-call field; several print the call instead."""
    from preauth.local.llm import tool_calls_in_text

    assert tool_calls_in_text('{"name": "verify_caller", "arguments": {"caller_reference": "PRV-30011"}}') == (
        __import__("preauth.local.llm", fromlist=["ToolCall"]).ToolCall(
            "verify_caller", {"caller_reference": "PRV-30011"}
        ),
    )
    fenced = tool_calls_in_text('Certainly.\n```json\n{"name":"log_transcript","arguments":{"summary":"x"}}\n```')
    assert [c.name for c in fenced] == ["log_transcript"]
    several = tool_calls_in_text('[{"name":"a","arguments":{}},{"name":"b","arguments":{}}]')
    assert [c.name for c in several] == ["a", "b"]


def test_ordinary_speech_is_never_mistaken_for_a_tool_call():
    from preauth.local.llm import tool_calls_in_text

    for text in (
        "Sawt Assurance pre-authorisation line. Who am I speaking with?",
        "Your case reference is PA-7K3M9Q2R. Anything else?",
        "The estimated cost was {21,000} AED.",
        "",
    ):
        assert tool_calls_in_text(text) == (), text


def test_a_recovered_call_still_goes_through_the_toolbox():
    """Recovery is not a bypass: an invented tool name fails exactly as a native one would."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": '{"name":"approve_case","arguments":{}}'}})

    reply = model_with(handler).chat([], [])
    assert reply.tool_calls[0].name == "approve_case"
    assert reply.text == ""  # the "reply" was the call; there is nothing to say to the caller
