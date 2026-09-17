"""ElevenLabs server-tool definitions generated from the toolbox.

ElevenLabs webhook tools accept a restricted body schema: flat properties of type string, integer, double or
boolean (optionally with an enum), each with a description. This module converts each tool's Pydantic input
model into that format and refuses anything that cannot be represented, so the voice platform and the backend
can never disagree about a tool's parameters.
"""

from typing import Any

from pydantic import BaseModel

from preauth.agent_tools.toolbox import TOOLS, Tool

VOICE_TOOL_PATH = "/api/v1/voice/tools/{tool_name}"
CONVERSATION_ID_VARIABLE = "system__conversation_id"
_SCALAR_TYPES = {"string", "integer", "boolean", "number"}


class UnsupportedToolSchemaError(ValueError):
    pass


def _resolve(prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    if "anyOf" in prop:
        branches = [b for b in prop["anyOf"] if b.get("type") != "null"]
        if len(branches) != 1:
            raise UnsupportedToolSchemaError(f"Union types are not supported: {prop}")
        return {**_resolve(branches[0], defs), **{k: v for k, v in prop.items() if k != "anyOf"}}
    if "$ref" in prop:
        target = defs[prop["$ref"].rsplit("/", 1)[-1]]
        return {**target, **{k: v for k, v in prop.items() if k != "$ref"}}
    return prop


def _property(name: str, prop: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve(prop, defs)
    description = prop.get("description") or resolved.get("description")
    if not description:
        raise UnsupportedToolSchemaError(f"Property {name!r} has no description")
    json_type = resolved.get("type")
    if json_type not in _SCALAR_TYPES:
        raise UnsupportedToolSchemaError(f"Property {name!r} has unsupported type {json_type!r}")
    out: dict[str, Any] = {"type": "double" if json_type == "number" else json_type, "description": description}
    if "enum" in resolved:
        out["enum"] = list(resolved["enum"])
    return out


def request_body_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    properties = {name: _property(name, prop, defs) for name, prop in schema.get("properties", {}).items()}
    return {
        "type": "object",
        "description": (model.__doc__ or f"Arguments for {model.__name__}").strip().splitlines()[0],
        "properties": properties,
        "required": list(schema.get("required", [])),
    }


def webhook_tool_config(
    tool: Tool,
    *,
    base_url: str,
    authorization_secret_id: str,
    response_timeout_secs: int = 20,
) -> dict[str, Any]:
    """One ElevenLabs ``tool_config`` for POST /v1/convai/tools.

    The Authorization header references a workspace secret whose value is ``Bearer <voice agent token>``; the
    conversation id is injected from the platform's system dynamic variable.
    """
    return {
        "type": "webhook",
        "name": tool.name,
        "description": tool.description,
        "response_timeout_secs": response_timeout_secs,
        "api_schema": {
            "url": base_url.rstrip("/") + VOICE_TOOL_PATH.format(tool_name=tool.name),
            "method": "POST",
            "request_headers": {
                "Authorization": {"secret_id": authorization_secret_id},
                "X-Conversation-ID": {"variable_name": CONVERSATION_ID_VARIABLE},
            },
            "request_body_schema": request_body_schema(tool.input_model),
        },
    }


def all_webhook_tool_configs(*, base_url: str, authorization_secret_id: str) -> list[dict[str, Any]]:
    return [
        webhook_tool_config(t, base_url=base_url, authorization_secret_id=authorization_secret_id) for t in TOOLS
    ]
