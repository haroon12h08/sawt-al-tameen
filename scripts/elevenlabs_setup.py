"""Create or update the ElevenLabs agent, its server tools, secret and knowledge base.

Everything the agent needs comes from this repository: tool schemas from ``preauth.agent_tools``, the system prompt
from ``voice/system_prompt.md``, knowledge-base documents from ``voice/knowledge_base``. Re-running updates in
place (IDs are kept in ``.elevenlabs-state.json``, which is git-ignored).

Required environment:
    ELEVENLABS_API_KEY          your ElevenLabs API key
    PREAUTH_PUBLIC_BASE_URL     public HTTPS URL of this backend, e.g. https://xyz.trycloudflare.com
    PREAUTH_VOICE_AGENT_TOKEN   the same token the backend is started with

Usage:
    uv run python scripts/elevenlabs_setup.py --dry-run   # print payloads, no network calls
    uv run python scripts/elevenlabs_setup.py             # create/update

Configured in the ElevenLabs dashboard instead (see docs/VOICE_AGENT.md): the visual workflow and per-node tool
scoping, the post-call webhook, evaluation criteria, agent tests, LLM cascading, and the phone number.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from preauth.agent_tools.elevenlabs import all_webhook_tool_configs
from preauth.seed.reference_data import COVERAGE, PROCEDURES, PROVIDERS

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / ".elevenlabs-state.json"
API = "https://api.elevenlabs.io"

AGENT_NAME = "Sawt Al Tameen - Provider Pre-Authorisation Desk"
DEFAULT_LLM = "gemini-2.5-flash"
DEFAULT_TTS_MODEL = "eleven_v3_conversational"
DEFAULT_VOICE_ID = "EXAVITQu4vr4xnSDxMaL"  # premade voice; replace with a Voice Design / Voice Library voice

FIRST_MESSAGE_EN = (
    "Pre-authorisation desk. This call is recorded for audit. May I have your name and the organisation you are "
    "calling from?"
)
FIRST_MESSAGE_AR = "مكتب التفويض المسبق. يتم تسجيل هذه المكالمة لأغراض التدقيق. هل يمكنني معرفة اسمك والجهة التي تتصل منها؟"


def keyterms() -> list[str]:
    """Domain vocabulary for speech recognition biasing: codes and terms callers will say aloud."""
    terms = [
        "pre-authorisation", "pre-authorization", "member ID", "policy number", "provider number", "ICD-10",
        "outpatient", "inpatient", "expedited", "conservative treatment", "clinical notes", "imaging report",
        "referral letter",
    ]
    terms += [number for number, *_ in PROVIDERS]
    terms += [code for code, *_ in PROCEDURES]
    terms += ["MRI", "arthroscopy", "meniscectomy", "polysomnography", "rhinoplasty"]
    terms += sorted({dx for (_, _, _, diagnoses, _, _) in COVERAGE.values() for dx in diagnoses} | {"Z80.3", "J34.2"})
    return list(dict.fromkeys(terms))


def knowledge_base_documents(include_uae: bool = False) -> dict[str, str]:
    """Documents uploaded to the agent's knowledge base.

    By default these are generated from the backend's own seed data, so the sources the rules cite are retrievable.
    ``--include-uae-knowledge-base`` adds the standalone synthetic UAE catalogue under knowledge_base/; the two
    describe different fictional product families, so loading both will give contradictory plan answers.
    """
    documents = {p.stem: p.read_text() for p in sorted((ROOT / "voice" / "knowledge_base").glob("*.md"))}
    if include_uae:
        for path in sorted((ROOT / "knowledge_base").glob("*")):
            if path.suffix in (".md", ".json"):
                documents[f"uae-{path.stem}"] = path.read_text()
    return documents


def system_tool(name: str) -> dict[str, Any]:
    return {"type": "system", "name": name, "description": "", "params": {"system_tool_type": name}}


def agent_payload(
    *, tool_ids: list[str], knowledge_base: list[dict[str, str]], llm: str, tts_model: str, voice_id: str
) -> dict[str, Any]:
    return {
        "name": AGENT_NAME,
        "tags": ["preauthorisation", "insurance", "uae", "b2b"],
        "conversation_config": {
            "agent": {
                "first_message": FIRST_MESSAGE_EN,
                "language": "en",
                "prompt": {
                    "prompt": (ROOT / "voice" / "system_prompt.md").read_text(),
                    "llm": llm,
                    "temperature": 0.1,
                    "tool_ids": tool_ids,
                    "built_in_tools": {
                        "end_call": system_tool("end_call"),
                        "language_detection": system_tool("language_detection"),
                    },
                    "knowledge_base": knowledge_base,
                },
            },
            "asr": {"keywords": keyterms()},
            "tts": {"voice_id": voice_id, "model_id": tts_model},
            "language_presets": {
                "ar": {"overrides": {"agent": {"first_message": FIRST_MESSAGE_AR, "language": "ar"}}},
            },
        },
    }


class ElevenLabs:
    def __init__(self, api_key: str):
        self._key = api_key

    def request(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            API + path,
            method=method,
            data=json.dumps(body).encode(),
            headers={"xi-api-key": self._key, "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise SystemExit(f"ElevenLabs API {method} {path} failed with {e.code}: {detail}") from None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="print payloads without calling the API")
    parser.add_argument("--llm", default=DEFAULT_LLM)
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    parser.add_argument("--voice-id", default=DEFAULT_VOICE_ID)
    parser.add_argument(
        "--include-uae-knowledge-base",
        action="store_true",
        help="also upload knowledge_base/ (the standalone synthetic UAE catalogue)",
    )
    args = parser.parse_args()
    documents = knowledge_base_documents(args.include_uae_knowledge_base)

    base_url = os.environ.get("PREAUTH_PUBLIC_BASE_URL", "").rstrip("/")
    token = os.environ.get("PREAUTH_VOICE_AGENT_TOKEN", "")
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if args.dry_run:
        base_url = base_url or "https://YOUR-PUBLIC-URL"
        tools = all_webhook_tool_configs(base_url=base_url, authorization_secret_id="<secret_id>")
        kb = [{"type": "text", "name": n, "id": "<document_id>", "usage_mode": "auto"} for n in documents]
        print(json.dumps({"tools": tools, "agent": agent_payload(
            tool_ids=["<tool_id>"] * len(tools), knowledge_base=kb,
            llm=args.llm, tts_model=args.tts_model, voice_id=args.voice_id,
        )}, indent=2, ensure_ascii=False))
        return 0

    missing = [n for n, v in [("ELEVENLABS_API_KEY", api_key), ("PREAUTH_PUBLIC_BASE_URL", base_url),
                              ("PREAUTH_VOICE_AGENT_TOKEN", token)] if not v]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not base_url.startswith("https://"):
        print("PREAUTH_PUBLIC_BASE_URL must be an https:// URL reachable from the internet", file=sys.stderr)
        return 2

    client = ElevenLabs(api_key)
    state: dict[str, Any] = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    # 1. Secret holding the Authorization header value (recreated when the token changes).
    if state.get("secret_token_digest") != _digest(token):
        secret = client.request(
            "POST", "/v1/convai/secrets",
            {"type": "new", "name": f"preauth_voice_bearer_{_digest(token)[:8]}", "value": f"Bearer {token}"},
        )
        state["secret_id"] = secret["secret_id"]
        state["secret_token_digest"] = _digest(token)
        print(f"secret: created {state['secret_id']}")

    # 2. Server tools.
    tool_ids = state.setdefault("tool_ids", {})
    for config in all_webhook_tool_configs(base_url=base_url, authorization_secret_id=state["secret_id"]):
        name = config["name"]
        if name in tool_ids:
            client.request("PATCH", f"/v1/convai/tools/{tool_ids[name]}", {"tool_config": config})
            print(f"tool: updated {name}")
        else:
            tool_ids[name] = client.request("POST", "/v1/convai/tools", {"tool_config": config})["id"]
            print(f"tool: created {name}")
    STATE_FILE.write_text(json.dumps(state, indent=2))

    # 3. Knowledge base (a new document is uploaded only when its content changed).
    kb_state = state.setdefault("knowledge_base", {})
    for name, text in documents.items():
        if kb_state.get(name, {}).get("digest") != _digest(text):
            created = client.request("POST", "/v1/convai/knowledge-base/text", {"name": name, "text": text})
            kb_state[name] = {"id": created["id"], "digest": _digest(text)}
            print(f"knowledge base: uploaded {name}")
    STATE_FILE.write_text(json.dumps(state, indent=2))
    knowledge_base = [
        {"type": "text", "name": name, "id": entry["id"], "usage_mode": "auto"}
        for name, entry in kb_state.items()
        if name in documents
    ]

    # 4. Agent.
    payload = agent_payload(
        tool_ids=[tool_ids[c["name"]] for c in all_webhook_tool_configs(base_url=base_url, authorization_secret_id="")],
        knowledge_base=knowledge_base,
        llm=args.llm,
        tts_model=args.tts_model,
        voice_id=args.voice_id,
    )
    if "agent_id" in state:
        client.request("PATCH", f"/v1/convai/agents/{state['agent_id']}", payload)
        print(f"agent: updated {state['agent_id']}")
    else:
        state["agent_id"] = client.request("POST", "/v1/convai/agents/create", payload)["agent_id"]
        print(f"agent: created {state['agent_id']}")
    STATE_FILE.write_text(json.dumps(state, indent=2))

    print(
        "\nDone. Remaining dashboard steps (docs/VOICE_AGENT.md): post-call webhook -> "
        f"{base_url}/api/v1/voice/elevenlabs/post-call, workflow + tool scoping, evaluation criteria, tests, "
        "phone number.\n"
        f"Test in the browser: https://elevenlabs.io/app/talk-to?agent_id={state['agent_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
