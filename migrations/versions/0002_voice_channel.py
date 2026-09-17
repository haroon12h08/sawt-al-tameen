"""voice channel

Adds the voice-channel tables (callback requests, voice tool invocations, call records), caller identity on
cases, source attribution on coverage terms, rule results and recommendations, and new audit event types.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ACTOR_TYPES = ("PROVIDER_PORTAL", "VOICE_AGENT", "SYSTEM", "HUMAN_REVIEWER")
CALLER_ROLES = ("PROVIDER_STAFF", "BROKER", "SUPPLIER", "OTHER")

AUDIT_EVENT_TYPES_0001 = (
    "CASE_CREATED", "CASE_STATUS_CHANGED", "INFORMATION_COLLECTED", "INFORMATION_MODIFIED", "DOCUMENT_REGISTERED",
    "VALIDATION_PASSED", "VALIDATION_FAILED", "RULES_EVALUATED", "RECOMMENDATION_GENERATED",
    "HUMAN_REVIEW_REQUESTED", "CASE_ESCALATED", "REVIEWER_ASSIGNED", "HUMAN_DECISION_RECORDED",
    "RECOMMENDATION_OVERRIDDEN", "CASE_CLOSED",
)
AUDIT_EVENT_TYPES_0002 = AUDIT_EVENT_TYPES_0001 + (
    "HUMAN_CALLBACK_REQUESTED", "HUMAN_CALLBACK_RESOLVED", "CALL_RECORDED",
)
NEW_APPEND_ONLY_TABLES = ("voice_tool_invocations", "call_records")


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=40)


# --------------------------------------------------------------------------- append-only triggers


def _create_append_only_triggers(tables: Sequence[str]) -> None:
    dialect = op.get_bind().dialect.name
    for table in tables:
        if dialect == "postgresql":
            # preauth_reject_mutation() was created in 0001.
            op.execute(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION preauth_reject_mutation()"
            )
        elif dialect == "sqlite":
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only; {operation} is not permitted'); END"
                )
        else:
            raise RuntimeError(f"Append-only triggers are not implemented for dialect {dialect!r}")


def _drop_append_only_triggers(tables: Sequence[str]) -> None:
    dialect = op.get_bind().dialect.name
    for table in tables:
        if dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        elif dialect == "sqlite":
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation}")


# --------------------------------------------------------------------------- audit event type constraint


def _audit_events_table(event_types: Sequence[str]) -> sa.Table:
    """Table definition used to rebuild audit_events on SQLite (which cannot alter CHECK constraints)."""
    meta = sa.MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    sa.Table("pre_authorization_cases", meta, sa.Column("id", sa.String(36), primary_key=True))
    return sa.Table(
        "audit_events",
        meta,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("pre_authorization_cases.id"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", _enum(event_types, "auditeventtype"), nullable=False),
        sa.Column("actor_type", _enum(ACTOR_TYPES, "actortype"), nullable=False),
        sa.Column("actor_id", sa.String(100), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("data", JSON, nullable=False),
        sa.UniqueConstraint("case_id", "sequence"),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        sa.Index("ix_audit_events_case_id", "case_id"),
    )


def _set_audit_event_types(old: Sequence[str], new: Sequence[str]) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        # op.f(): the name is already final; do not apply the naming convention a second time.
        op.drop_constraint(op.f("ck_audit_events_auditeventtype"), "audit_events", type_="check")
        quoted = ", ".join(f"'{v}'" for v in new)
        op.create_check_constraint(
            op.f("ck_audit_events_auditeventtype"), "audit_events", f"event_type IN ({quoted})"
        )
    elif dialect == "sqlite":
        # Rebuilding the table drops its triggers; drop them explicitly first and reinstall afterwards.
        _drop_append_only_triggers(["audit_events"])
        with op.batch_alter_table(
            "audit_events", copy_from=_audit_events_table(old), recreate="always"
        ) as batch_op:
            batch_op.alter_column(
                "event_type",
                existing_type=_enum(old, "auditeventtype"),
                type_=_enum(new, "auditeventtype"),
                existing_nullable=False,
            )
        _create_append_only_triggers(["audit_events"])
    else:
        raise RuntimeError(f"Unsupported dialect {dialect!r}")


# --------------------------------------------------------------------------- migration


def upgrade() -> None:
    op.create_table(
        "call_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=100), nullable=False),
        sa.Column("agent_id", sa.String(length=100), nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=True),
        sa.Column("call_duration_secs", sa.Integer(), nullable=True),
        sa.Column("transcript_summary", sa.Text(), nullable=True),
        sa.Column("call_successful", sa.String(length=40), nullable=True),
        sa.Column("transcript", JSON, nullable=False),
        sa.Column("analysis", JSON, nullable=False),
        sa.Column("call_metadata", JSON, nullable=False),
        sa.Column("event_timestamp", sa.BigInteger(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_records")),
        sa.UniqueConstraint("conversation_id", name=op.f("uq_call_records_conversation_id")),
    )
    op.create_table(
        "callback_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("reference", sa.String(length=20), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=True),
        sa.Column("conversation_id", sa.String(length=100), nullable=True),
        sa.Column("caller_name", sa.String(length=100), nullable=False),
        sa.Column("caller_organisation", sa.String(length=200), nullable=True),
        sa.Column("caller_role", _enum(CALLER_ROLES, "callerrole"), nullable=False),
        sa.Column("callback_phone", sa.String(length=20), nullable=False),
        sa.Column("preferred_language", sa.String(length=5), nullable=False),
        sa.Column(
            "reason",
            _enum(
                ("NON_STANDARD_REQUEST", "CALLER_REQUESTED_HUMAN", "SUPPLIER_ENQUIRY", "URGENT_CLINICAL",
                 "COMPLAINT", "UNSUPPORTED_LANGUAGE", "OTHER"),
                "callbackreason",
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", _enum(("OPEN", "RESOLVED"), "callbackstatus"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_actor_type", _enum(ACTOR_TYPES, "actortype"), nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=100), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"], ["pre_authorization_cases.id"],
            name=op.f("fk_callback_requests_case_id_pre_authorization_cases"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_callback_requests")),
        sa.UniqueConstraint("reference", name=op.f("uq_callback_requests_reference")),
    )
    op.create_index(op.f("ix_callback_requests_case_id"), "callback_requests", ["case_id"], unique=False)
    op.create_index(
        op.f("ix_callback_requests_conversation_id"), "callback_requests", ["conversation_id"], unique=False
    )
    op.create_index(op.f("ix_callback_requests_status"), "callback_requests", ["status"], unique=False)

    op.create_table(
        "voice_tool_invocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=100), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=True),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("invoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["pre_authorization_cases.id"],
            name=op.f("fk_voice_tool_invocations_case_id_pre_authorization_cases"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_tool_invocations")),
    )
    op.create_index(
        op.f("ix_voice_tool_invocations_case_id"), "voice_tool_invocations", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_voice_tool_invocations_conversation_id"), "voice_tool_invocations", ["conversation_id"],
        unique=False,
    )

    # Nullable additions to existing tables: plain ADD COLUMN, no table rebuild.
    op.add_column("coverage_terms", sa.Column("source_document", sa.String(length=200), nullable=True))
    op.add_column("coverage_terms", sa.Column("source_section", sa.String(length=100), nullable=True))
    op.add_column("pre_authorization_cases", sa.Column("caller_name", sa.String(length=100), nullable=True))
    op.add_column("pre_authorization_cases", sa.Column("caller_role", sa.String(length=40), nullable=True))
    op.add_column(
        "recommendations", sa.Column("sources", JSON, server_default=sa.text("'[]'"), nullable=False)
    )
    op.add_column("rule_results", sa.Column("sources", JSON, server_default=sa.text("'[]'"), nullable=False))

    _set_audit_event_types(AUDIT_EVENT_TYPES_0001, AUDIT_EVENT_TYPES_0002)
    _create_append_only_triggers(NEW_APPEND_ONLY_TABLES)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    _drop_append_only_triggers(NEW_APPEND_ONLY_TABLES)
    # Downgrading fails if audit_events already contains rows using the 0002 event types; that is intended,
    # because audit history must never be silently discarded.
    _set_audit_event_types(AUDIT_EVENT_TYPES_0002, AUDIT_EVENT_TYPES_0001)

    if dialect == "sqlite":
        # recommendations carries append-only triggers; SQLite's native DROP COLUMN keeps them.
        op.execute("ALTER TABLE recommendations DROP COLUMN sources")
        op.execute("ALTER TABLE rule_results DROP COLUMN sources")
        op.execute("ALTER TABLE pre_authorization_cases DROP COLUMN caller_role")
        op.execute("ALTER TABLE pre_authorization_cases DROP COLUMN caller_name")
        op.execute("ALTER TABLE coverage_terms DROP COLUMN source_section")
        op.execute("ALTER TABLE coverage_terms DROP COLUMN source_document")
    else:
        op.drop_column("recommendations", "sources")
        op.drop_column("rule_results", "sources")
        op.drop_column("pre_authorization_cases", "caller_role")
        op.drop_column("pre_authorization_cases", "caller_name")
        op.drop_column("coverage_terms", "source_section")
        op.drop_column("coverage_terms", "source_document")

    op.drop_index(op.f("ix_voice_tool_invocations_conversation_id"), table_name="voice_tool_invocations")
    op.drop_index(op.f("ix_voice_tool_invocations_case_id"), table_name="voice_tool_invocations")
    op.drop_table("voice_tool_invocations")
    op.drop_index(op.f("ix_callback_requests_status"), table_name="callback_requests")
    op.drop_index(op.f("ix_callback_requests_conversation_id"), table_name="callback_requests")
    op.drop_index(op.f("ix_callback_requests_case_id"), table_name="callback_requests")
    op.drop_table("callback_requests")
    op.drop_table("call_records")
