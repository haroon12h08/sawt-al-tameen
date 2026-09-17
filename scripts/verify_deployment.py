"""End-to-end check of a running deployment, exercising exactly what the voice agent will do.

Run this against your public URL before pointing ElevenLabs at it. It walks a synthetic pre-authorisation call
from first tool call to human sign-off, and verifies the guardrails along the way.

    export PREAUTH_VOICE_AGENT_TOKEN=...            # required
    export PREAUTH_GATEWAY_SECRET=...               # if the deployment sets one
    export PREAUTH_ELEVENLABS_WEBHOOK_SECRET=...    # to test the post-call webhook
    uv run python scripts/verify_deployment.py --base-url https://your-backend.example

The deployment must have the synthetic reference data loaded (`python -m preauth.seed`). The case this creates is
closed at the end, and everything it touches is synthetic.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from typing import Any

from preauth.infrastructure.elevenlabs_signature import sign

CONVERSATION_ID = f"verify_{uuid.uuid4().hex[:12]}"
MEMBER = {"patient_member_id": "MBR-5001-01", "patient_date_of_birth": "1984-03-12"}

passed: list[str] = []
failed: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    (passed if ok else failed).append(name)
    if ok and detail in ("None", "{}"):
        detail = ""
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    return ok


class Client:
    def __init__(self, base_url: str, voice_token: str, gateway_secret: str | None):
        self.base = base_url.rstrip("/")
        self.voice_headers = {"Authorization": f"Bearer {voice_token}", "X-Conversation-ID": CONVERSATION_ID}
        self.gateway = {"X-Gateway-Secret": gateway_secret} if gateway_secret else {}

    def request(
        self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None, raw: bytes | None = None
    ) -> tuple[int, Any]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"content-type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read()
                return resp.status, json.loads(payload) if payload else None
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except ValueError:
                return e.code, payload.decode(errors="replace")
        except urllib.error.URLError as e:
            return 0, str(e)

    def tool(self, name: str, arguments: dict[str, Any]) -> Any:
        status, body = self.request("POST", f"/api/v1/voice/tools/{name}", arguments, self.voice_headers)
        if status != 200:
            return {"ok": False, "error": {"code": f"HTTP_{status}", "message": str(body)}}
        return body

    def staff(self, method: str, path: str, body: Any = None, actor: dict[str, str] | None = None) -> tuple[int, Any]:
        headers = {**self.gateway, **(actor or {"X-Actor-Type": "PROVIDER_PORTAL", "X-Actor-Id": "verify-script"})}
        return self.request(method, path, body, headers)


REVIEWER = {"X-Actor-Type": "HUMAN_REVIEWER", "X-Actor-Id": "verify-reviewer", "X-Actor-Roles": "CLINICAL_REVIEWER"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("PREAUTH_PUBLIC_BASE_URL", "http://localhost:8000"))
    args = parser.parse_args()

    token = os.environ.get("PREAUTH_VOICE_AGENT_TOKEN", "").strip()
    if not token:
        print("PREAUTH_VOICE_AGENT_TOKEN is not set", file=sys.stderr)
        return 2
    gateway_secret = os.environ.get("PREAUTH_GATEWAY_SECRET", "").strip() or None
    webhook_secret = os.environ.get("PREAUTH_ELEVENLABS_WEBHOOK_SECRET", "").strip() or None
    client = Client(args.base_url, token, gateway_secret)
    print(f"Verifying {client.base}  (conversation {CONVERSATION_ID})\n")

    print("Reachability and authentication")
    status, body = client.request("GET", "/health")
    if not check("health endpoint responds", status == 200 and body == {"status": "ok"}, str(body)[:80]):
        print("\nBackend not reachable; nothing else can be checked.")
        return 1
    status, _ = client.request("POST", "/api/v1/voice/tools/get_case_status", {}, {"Authorization": "Bearer wrong"})
    check("voice tools reject a wrong token", status == 401, f"got HTTP {status}")
    if gateway_secret:
        status, _ = client.request("GET", "/api/v1/review/queues/CLINICAL_REVIEW", headers=REVIEWER)
        check("staff API rejects a missing gateway secret", status == 401, f"got HTTP {status}")
    status, _ = client.staff("GET", "/api/v1/review/queues/CLINICAL_REVIEW", actor=REVIEWER)
    check("reviewer API reachable with credentials", status == 200, f"got HTTP {status}")

    print("\nIntake (as the voice agent)")
    created = client.tool(
        "create_pre_authorization_case",
        {"caller_name": "Verification Script", "caller_role": "PROVIDER_STAFF", "provider_number": "PRV-100234"},
    )
    if not created.get("ok"):
        code = created["error"]["code"]
        check("case created", False, code)
        if code == "UNKNOWN_PROVIDER":
            print("\nThe deployment has no reference data. Run: python -m preauth.seed")
        return 1
    case = created["result"]
    case_id, reference = case["id"], case["case_reference"]
    check("case created", True, reference)

    wrong_dob = client.tool("submit_information", {"case_id": case_id, **{**MEMBER, "patient_date_of_birth": "1990-01-01"}})
    check(
        "wrong date of birth is refused with guidance",
        not wrong_dob.get("ok") and wrong_dob["error"]["code"] == "MEMBER_NOT_VERIFIED" and bool(wrong_dob.get("guidance")),
        str((wrong_dob.get("error") or {}).get("code")),
    )

    service_date = (date.today() + timedelta(days=14)).isoformat()
    intake = client.tool(
        "submit_information",
        {
            "case_id": case_id, **MEMBER, "policy_number": "POL-000101", "procedure_code": "PROC-MRI-KNEE",
            "requested_service_date": service_date, "place_of_service": "OUTPATIENT", "diagnosis_code": "M23.221",
            "urgency": "STANDARD", "conservative_treatment_weeks": 8,
        },
    )
    check("intake accepted", intake.get("ok") is True, str((intake.get("error") or {}).get("code")))

    needed = client.tool("get_required_information", {"case_id": case_id})
    missing_codes = [m["code"] for m in needed.get("result", {}).get("missing_information", [])]
    check("required information lists the missing document", missing_codes == ["document.CLINICAL_NOTES"], str(missing_codes))

    print("\nRules, documents and recommendation")
    evaluated = client.tool("evaluate_case", {"case_id": case_id})
    check(
        "evaluation asks for the missing document",
        evaluated.get("result", {}).get("status") == "PENDING_INFORMATION",
        str(evaluated.get("result", {}).get("status")),
    )
    status, _ = client.staff(
        "POST", f"/api/v1/cases/{case_id}/documents",
        {"document_type": "CLINICAL_NOTES", "title": "Verification note",
         "storage_uri": "docstore://verify/notes.pdf", "media_type": "application/pdf"},
    )
    check("document registered through the provider API", status == 201, f"got HTTP {status}")

    evaluated = client.tool("evaluate_case", {"case_id": case_id})
    result = evaluated.get("result", {})
    recommendation = result.get("recommendation") or {}
    check("case is ready for review", result.get("status") == "RECOMMENDATION_READY", str(result.get("status")))
    check(
        "recommendation is advisory and cites sources",
        recommendation.get("advisory_only") is True and bool(recommendation.get("sources")),
        f"{len(recommendation.get('sources', []))} sources",
    )
    routed = client.tool("request_human_review", {"case_id": case_id})
    check(
        "case routed to a human reviewer",
        routed.get("result", {}).get("status") == "PENDING_HUMAN_REVIEW",
        str(routed.get("result", {}).get("status")),
    )

    print("\nGuardrails")
    for name in ("approve_case", "record_decision"):
        response = client.tool(name, {"case_id": case_id})
        check(f"no {name} tool exists", response["error"]["code"] == "TOOL_NOT_FOUND", str(response["error"]["code"]))

    status, _ = client.staff("POST", f"/api/v1/review/cases/{case_id}/assignment", actor=REVIEWER)
    check("reviewer can self-assign", status == 200, f"got HTTP {status}")
    decision = {
        "recommendation_id": recommendation.get("id", ""),
        "decision": "APPROVE",
        "rationale": "Deployment verification: criteria met and documentation present.",
    }
    status, body = client.staff("POST", f"/api/v1/review/cases/{case_id}/decision", decision, actor=REVIEWER)
    blocked = status == 409 and (body.get("error") or {}).get("code") == "CALL_RECORD_PENDING"
    check("sign-off blocked until the call transcript is logged", blocked, str((body.get("error") or {}).get("code")))

    print("\nPost-call webhook")
    if webhook_secret:
        payload = {
            "type": "post_call_transcription",
            "event_timestamp": int(time.time()),
            "data": {
                "agent_id": "verification", "conversation_id": CONVERSATION_ID, "status": "done",
                "transcript": [{"role": "agent", "message": "Deployment verification call."}],
                "metadata": {"call_duration_secs": 42},
                "analysis": {"transcript_summary": "Deployment verification.", "call_successful": "success"},
            },
        }
        raw = json.dumps(payload).encode()
        status, _ = client.request(
            "POST", "/api/v1/voice/elevenlabs/post-call", raw=raw,
            headers={"elevenlabs-signature": sign(raw, "wrong-secret", int(time.time()))},
        )
        check("webhook rejects a bad signature", status == 401, f"got HTTP {status}")
        status, body = client.request(
            "POST", "/api/v1/voice/elevenlabs/post-call", raw=raw,
            headers={"elevenlabs-signature": sign(raw, webhook_secret, int(time.time()))},
        )
        accepted = status == 200 and body.get("accepted") is True
        check("webhook stores the transcript", accepted, str(body)[:80])
        check("transcript is linked to the case", case_id in (body.get("linked_case_ids") or []), str(body.get("linked_case_ids")))

        status, body = client.staff("POST", f"/api/v1/review/cases/{case_id}/decision", decision, actor=REVIEWER)
        check("sign-off succeeds once logged", status == 201 and body.get("to_status") == "APPROVED", f"HTTP {status}")
        status, _ = client.staff(
            "POST", f"/api/v1/cases/{case_id}/closure", {"reason": "DECISION_COMMUNICATED"},
            actor={"X-Actor-Type": "SYSTEM", "X-Actor-Id": "verify-script"},
        )
        check("case closed", status == 200, f"got HTTP {status}")
    else:
        print("  SKIP  post-call webhook (PREAUTH_ELEVENLABS_WEBHOOK_SECRET not set)")
        print("        Reviewers stay blocked on voice cases until the webhook is configured.")

    print(f"\n{len(passed)} passed, {len(failed)} failed")
    if failed:
        print("Failed: " + ", ".join(failed))
        return 1
    print(f"Deployment looks good. Case {reference} was created and cleaned up.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
