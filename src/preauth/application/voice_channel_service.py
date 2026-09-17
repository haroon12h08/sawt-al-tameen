"""Voice-channel bookkeeping: which conversations touched which cases, and the post-call record of each call.

The post-call record is what makes a voice interaction auditable. A human decision on a case is blocked until
every conversation that touched the case has delivered its transcript (see ``pending_conversation_ids``).
"""

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session, sessionmaker

from preauth.application.unit_of_work import UnitOfWork
from preauth.domain.actors import SYSTEM_ACTOR
from preauth.domain.enums import AuditEventType
from preauth.domain.errors import NotFoundError
from preauth.infrastructure import elevenlabs_signature
from preauth.infrastructure.clock import Clock, new_id
from preauth.infrastructure.db.models import CallRecord, VoiceToolInvocation
from preauth.domain.actors import Actor
from preauth.infrastructure.observability import actor_var, bind_case_id, conversation_id_var, request_id_var

logger = logging.getLogger("preauth.voice")


class _Lenient(BaseModel):
    """External payload: ignore fields we do not use, so platform additions do not break ingestion."""

    model_config = ConfigDict(extra="ignore")


class PostCallData(_Lenient):
    agent_id: str
    conversation_id: str
    status: str | None = None
    transcript: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    analysis: dict[str, Any] | None = None


class PostCallEvent(_Lenient):
    type: str
    event_timestamp: int | None = None
    data: dict[str, Any]


class PostCallOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    accepted: bool
    detail: str
    call_record_id: str | None = None
    linked_case_ids: list[str] = []


def bind_voice_context(actor: Actor, conversation_id: str | None) -> None:
    """Attach the voice actor and conversation to logs, audit linkage and callback records for this request."""
    actor_var.set(f"{actor.type.value}:{actor.id}")
    conversation_id_var.set(conversation_id)


def pending_conversation_ids(uow: UnitOfWork, case_id: str) -> list[str]:
    conversations = uow.voice.conversation_ids_for_case(case_id)
    recorded = {r.conversation_id for r in uow.voice.call_records(conversations)}
    return [c for c in conversations if c not in recorded]


class VoiceChannelService:
    PLATFORM = "elevenlabs"

    def __init__(self, session_factory: sessionmaker[Session], clock: Clock):
        self._session_factory = session_factory
        self._clock = clock

    @staticmethod
    def verify_webhook_signature(raw_body: bytes, signature_header: str | None, secret: str) -> None:
        elevenlabs_signature.verify(raw_body, signature_header, secret)

    def record_tool_invocation(
        self, *, tool_name: str, case_id: str | None, succeeded: bool, error_code: str | None
    ) -> None:
        conversation_id = conversation_id_var.get()
        if conversation_id is None:
            return  # Not a voice conversation (e.g. direct API test); nothing to link.
        with UnitOfWork(self._session_factory, self._clock) as uow:
            if case_id is not None:
                try:
                    uow.cases.get(case_id)
                except NotFoundError:
                    case_id = None  # the tool referenced a case that does not exist; keep the invocation, drop link
            uow.voice.add_invocation(
                VoiceToolInvocation(
                    id=new_id(),
                    conversation_id=conversation_id,
                    tool_name=tool_name,
                    case_id=case_id,
                    succeeded=succeeded,
                    error_code=error_code,
                    request_id=request_id_var.get(),
                    invoked_at=self._clock.now(),
                )
            )
            uow.commit()

    def record_post_call(self, event: PostCallEvent) -> PostCallOutcome:
        if event.type != "post_call_transcription":
            logger.info("post_call_event_ignored", extra={"event_type": event.type})
            return PostCallOutcome(accepted=False, detail=f"Event type {event.type!r} is not stored")

        data = PostCallData.model_validate(event.data)
        conversation_id_var.set(data.conversation_id)
        with UnitOfWork(self._session_factory, self._clock) as uow:
            existing = uow.voice.call_record(data.conversation_id)
            if existing is not None:
                # The platform retries deliveries; storing twice would duplicate the audit trail.
                return PostCallOutcome(
                    accepted=True,
                    detail="Already recorded",
                    call_record_id=existing.id,
                    linked_case_ids=uow.voice.case_ids_for_conversation(data.conversation_id),
                )

            analysis = data.analysis or {}
            duration = data.metadata.get("call_duration_secs")
            record = CallRecord(
                id=new_id(),
                conversation_id=data.conversation_id,
                agent_id=data.agent_id,
                platform=self.PLATFORM,
                status=data.status,
                call_duration_secs=duration if isinstance(duration, int) else None,
                transcript_summary=analysis.get("transcript_summary"),
                call_successful=analysis.get("call_successful"),
                transcript=data.transcript,
                analysis=analysis,
                call_metadata=data.metadata,
                event_timestamp=event.event_timestamp,
                received_at=self._clock.now(),
            )
            uow.voice.add_call_record(record)
            uow.flush()

            case_ids = uow.voice.case_ids_for_conversation(data.conversation_id)
            for case_id in case_ids:
                bind_case_id(case_id)
                uow.audit.record(
                    case_id,
                    AuditEventType.CALL_RECORDED,
                    SYSTEM_ACTOR,
                    {
                        "call_record_id": record.id,
                        "conversation_id": data.conversation_id,
                        "agent_id": data.agent_id,
                        "call_duration_secs": record.call_duration_secs,
                        "call_successful": record.call_successful,
                        "transcript_summary": record.transcript_summary,
                        "transcript_turns": len(data.transcript),
                    },
                )
            uow.commit()
            logger.info("call_recorded", extra={"linked_cases": len(case_ids), "turns": len(data.transcript)})
            return PostCallOutcome(
                accepted=True, detail="Recorded", call_record_id=record.id, linked_case_ids=case_ids
            )
