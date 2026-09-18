"""Conversation state for a local call.

The model is not trusted to remember anything that matters. Two kinds of state exist:

* **Durable facts** already live in the database, written by the tools themselves — the caller verification row,
  the case, its documents, its rule evaluation, its recommendation and its audit trail. Nothing here duplicates
  them.
* **In-flight conversation state** — the turns so far, and which intake fields have been gathered — lives in this
  process for the length of the call and is written to the database as a call record when the call ends.

A session is identified by a stable ``conversation_id`` that is passed to every tool call, so the existing
"transcript before sign-off" rule applies to local calls exactly as it does to telephone calls.
"""

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from preauth.agent_tools.toolbox import CheckCoverageRuleInput
from preauth.local.errors import ConversationNotFoundError

# The fields a coverage check cannot run without, taken from the tool's own input model so the two cannot drift.
# ``verification_id`` is excluded: it is produced by verify_caller, not collected from the caller.
REQUIRED_INTAKE_FIELDS: tuple[str, ...] = tuple(
    f for f in CheckCoverageRuleInput.model_json_schema()["required"] if f != "verification_id"
)
OPTIONAL_INTAKE_FIELDS: tuple[str, ...] = ("urgency", "diagnosis_code", "clinical_summary")


def new_conversation_id() -> str:
    """Matches the conversation-id pattern the voice gateway accepts, and is obviously local at a glance."""
    return f"local_{secrets.token_hex(8)}"


@dataclass(frozen=True)
class Turn:
    role: str  # "agent", "caller" or "tool"
    text: str
    at: datetime
    tool_name: str | None = None
    tool_ok: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "text": self.text,
            "at": self.at.isoformat(),
            "tool_name": self.tool_name,
            "tool_ok": self.tool_ok,
        }


@dataclass
class LocalSession:
    conversation_id: str
    started_at: datetime
    # Chat history in the model's own format. Rebuilt from nothing but tool results and spoken turns.
    messages: list[dict[str, Any]] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    # Durable references, echoed here so the UI and the model can quote them without another query.
    verification_id: str | None = None
    verified: bool = False
    # A caller can be verified without a member (organisation only). A coverage check needs both.
    member_verified: bool = False
    case_id: str | None = None
    case_reference: str | None = None
    case_status: str | None = None
    recommendation_outcome: str | None = None
    escalation_rule_ids: list[str] = field(default_factory=list)
    missing_documents: list[str] = field(default_factory=list)
    call_log_reference: str | None = None
    intake: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finished_at: datetime | None = None
    call_record_id: str | None = None

    @property
    def missing_intake_fields(self) -> list[str]:
        return [f for f in REQUIRED_INTAKE_FIELDS if self.intake.get(f) in (None, "")]

    @property
    def intake_complete(self) -> bool:
        return self.verified and self.member_verified and not self.missing_intake_fields

    def record_turn(self, turn: Turn) -> None:
        self.turns.append(turn)

    def note_intake(self, arguments: dict[str, Any]) -> None:
        """Remember what the caller has already given, so the agent never asks for the same value twice."""
        for name in REQUIRED_INTAKE_FIELDS + OPTIONAL_INTAKE_FIELDS:
            value = arguments.get(name)
            if value not in (None, ""):
                self.intake[name] = value

    def transcript(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in self.turns]

    def state(self) -> dict[str, Any]:
        """What the browser shows: verification, case, tools, escalation and human-review status."""
        return {
            "conversation_id": self.conversation_id,
            "verified": self.verified,
            "member_verified": self.member_verified,
            "verification_id": self.verification_id,
            "case_reference": self.case_reference,
            "case_id": self.case_id,
            "case_status": self.case_status,
            "recommendation_outcome": self.recommendation_outcome,
            "escalation_rule_ids": list(self.escalation_rule_ids),
            "missing_documents": list(self.missing_documents),
            "call_log_reference": self.call_log_reference,
            "intake": dict(self.intake),
            "missing_intake_fields": self.missing_intake_fields,
            "intake_complete": self.intake_complete,
            # Local mode prepares; a qualified human decides. This never changes inside a call.
            "human_review_status": self._human_review_status(),
            "finished": self.finished_at is not None,
            "call_record_id": self.call_record_id,
        }

    def _human_review_status(self) -> str:
        if self.case_reference is None:
            return "NO_CASE_YET"
        if self.recommendation_outcome == "REQUEST_MORE_INFORMATION":
            return "AWAITING_DOCUMENTS"
        if self.call_record_id is None:
            return "SIGN_OFF_BLOCKED_UNTIL_TRANSCRIPT_STORED"
        return "AWAITING_HUMAN_REVIEWER"


class SessionStore:
    """In-process registry of live calls. One process serves one local desk; there is nothing to share."""

    def __init__(self) -> None:
        self._sessions: dict[str, LocalSession] = {}
        self._lock = threading.Lock()

    def create(self, started_at: datetime) -> LocalSession:
        session = LocalSession(conversation_id=new_conversation_id(), started_at=started_at)
        with self._lock:
            self._sessions[session.conversation_id] = session
        return session

    def get(self, conversation_id: str) -> LocalSession:
        with self._lock:
            session = self._sessions.get(conversation_id)
        if session is None:
            raise ConversationNotFoundError(
                "No local conversation with that id is in progress",
                details={"conversation_id": conversation_id},
            )
        return session

    def ids(self) -> list[str]:
        with self._lock:
            return list(self._sessions)
