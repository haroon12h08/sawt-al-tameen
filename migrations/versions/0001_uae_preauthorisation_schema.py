"""UAE pre-authorisation schema

Single initial migration. Creates the benefit catalogue (tiers, procedures, coverage, providers, members,
escalation rules, onboarding), case management, evaluation, review, audit and voice-channel tables, and installs
triggers that make the decision trail append-only on PostgreSQL and SQLite.

Revision ID: 0001
Revises: 
Create Date: 2026-09-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPEND_ONLY_TABLES = (
    "rule_evaluations", "rule_results", "recommendations", "review_decisions", "audit_events",
    "voice_tool_invocations", "call_records", "caller_verifications", "call_logs",
)


def _install_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION preauth_reject_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table in APPEND_ONLY_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION preauth_reject_mutation()"
            )
    elif dialect == "sqlite":
        for table in APPEND_ONLY_TABLES:
            for operation in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{operation.lower()} BEFORE {operation} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only; {operation} is not permitted'); END"
                )
    else:
        raise RuntimeError(f"Append-only triggers are not implemented for dialect {dialect!r}")


def _drop_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in APPEND_ONLY_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS preauth_reject_mutation()")
    elif dialect == "sqlite":
        for table in APPEND_ONLY_TABLES:
            for operation in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{operation}")


def upgrade() -> None:
    op.create_table('call_records',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=100), nullable=False),
    sa.Column('agent_id', sa.String(length=100), nullable=False),
    sa.Column('platform', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=40), nullable=True),
    sa.Column('call_duration_secs', sa.Integer(), nullable=True),
    sa.Column('transcript_summary', sa.Text(), nullable=True),
    sa.Column('call_successful', sa.String(length=40), nullable=True),
    sa.Column('transcript', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('analysis', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('call_metadata', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('event_timestamp', sa.BigInteger(), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_records')),
    sa.UniqueConstraint('conversation_id', name=op.f('uq_call_records_conversation_id'))
    )
    op.create_table('escalation_rules',
    sa.Column('rule_id', sa.String(length=20), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('situation', sa.Text(), nullable=False),
    sa.Column('agent_action', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('rule_id', name=op.f('pk_escalation_rules'))
    )
    op.create_table('onboarding_requirements',
    sa.Column('requirement_id', sa.String(length=20), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('issuing_authority', sa.String(length=200), nullable=False),
    sa.Column('mandatory', sa.Boolean(), nullable=False),
    sa.Column('validity_months', sa.Integer(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('requirement_id', name=op.f('pk_onboarding_requirements'))
    )
    op.create_table('policy_tiers',
    sa.Column('tier_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('product_name', sa.String(length=200), nullable=False),
    sa.Column('tier_rank', sa.Integer(), nullable=False),
    sa.Column('annual_limit_aed', sa.Integer(), nullable=False),
    sa.Column('pre_authorisation_threshold_aed', sa.Integer(), nullable=False),
    sa.Column('sub_limits_aed', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('co_payments_percent', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('co_payment_caps_aed', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('waiting_periods_months', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('network_id', sa.String(length=60), nullable=False),
    sa.Column('network_name', sa.String(length=80), nullable=False),
    sa.Column('out_of_network_covered', sa.Boolean(), nullable=False),
    sa.Column('source_document', sa.String(length=200), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('tier_id', name=op.f('pk_policy_tiers'))
    )
    op.create_table('providers',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('provider_number', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('emirate', sa.String(length=60), nullable=False),
    sa.Column('area', sa.String(length=80), nullable=False),
    sa.Column('facility_type', sa.String(length=40), nullable=False),
    sa.Column('regulator', sa.String(length=20), nullable=False),
    sa.Column('facility_licence_number', sa.String(length=60), nullable=False),
    sa.Column('minimum_network_rank', sa.Integer(), nullable=False),
    sa.Column('specialties', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('directory_status', sa.Enum('ACTIVE', 'SUSPENDED', 'PENDING_ONBOARDING', name='directorystatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_providers')),
    sa.UniqueConstraint('provider_number', name=op.f('uq_providers_provider_number'))
    )
    op.create_table('rule_definitions',
    sa.Column('rule_id', sa.String(length=80), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('category', sa.Enum('ELIGIBILITY', 'NETWORK', 'COVERAGE', 'DOCUMENTATION', 'LIMITS', name='rulecategory', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('first_registered_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('rule_id', 'version', name=op.f('pk_rule_definitions'))
    )
    op.create_table('members',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('member_id', sa.String(length=40), nullable=False),
    sa.Column('policy_number', sa.String(length=40), nullable=False),
    sa.Column('given_name', sa.String(length=100), nullable=False),
    sa.Column('family_name', sa.String(length=100), nullable=False),
    sa.Column('nationality', sa.String(length=60), nullable=False),
    sa.Column('date_of_birth', sa.Date(), nullable=False),
    sa.Column('emirates_id', sa.String(length=30), nullable=False),
    sa.Column('mobile', sa.String(length=20), nullable=True),
    sa.Column('tier_id', sa.String(length=40), nullable=False),
    sa.Column('policy_status', sa.Enum('ACTIVE', 'LAPSED', name='policystatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('policy_start_date', sa.Date(), nullable=False),
    sa.Column('policy_renewal_date', sa.Date(), nullable=True),
    sa.Column('policy_lapse_date', sa.Date(), nullable=True),
    sa.Column('emirate_of_residence', sa.String(length=60), nullable=False),
    sa.Column('sponsor', sa.String(length=200), nullable=True),
    sa.Column('dependents', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['tier_id'], ['policy_tiers.tier_id'], name=op.f('fk_members_tier_id_policy_tiers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_members')),
    sa.UniqueConstraint('emirates_id', name=op.f('uq_members_emirates_id')),
    sa.UniqueConstraint('member_id', name=op.f('uq_members_member_id')),
    sa.UniqueConstraint('policy_number', name=op.f('uq_members_policy_number'))
    )
    op.create_index(op.f('ix_members_tier_id'), 'members', ['tier_id'], unique=False)

    op.create_table('onboarding_applications',
    sa.Column('application_id', sa.String(length=40), nullable=False),
    sa.Column('provider_id', sa.String(length=36), nullable=False),
    sa.Column('provider_name', sa.String(length=200), nullable=False),
    sa.Column('provider_status', sa.String(length=40), nullable=False),
    sa.Column('documents', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('outstanding', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], name=op.f('fk_onboarding_applications_provider_id_providers')),
    sa.PrimaryKeyConstraint('application_id', name=op.f('pk_onboarding_applications'))
    )
    op.create_index(op.f('ix_onboarding_applications_provider_id'), 'onboarding_applications', ['provider_id'], unique=False)

    op.create_table('procedures',
    sa.Column('procedure_code', sa.String(length=40), nullable=False),
    sa.Column('code_system', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=300), nullable=False),
    sa.Column('category', sa.String(length=40), nullable=False),
    sa.Column('specialty_required', sa.String(length=80), nullable=False),
    sa.Column('typical_billed_amount_aed', sa.Integer(), nullable=False),
    sa.Column('pre_authorisation_rule', sa.String(length=40), nullable=False),
    sa.Column('minimum_tier', sa.String(length=40), nullable=False),
    sa.Column('waiting_period_months', sa.Integer(), nullable=False),
    sa.Column('waiting_period_waived_for_emergency', sa.Boolean(), nullable=False),
    sa.Column('decision_class', sa.Enum('CLEAR', 'EXCLUDED', 'AMBIGUOUS', name='decisionclass', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('escalation_rule_id', sa.String(length=20), nullable=True),
    sa.Column('escalation_reason', sa.Text(), nullable=True),
    sa.Column('exclusions', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('required_documents', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['escalation_rule_id'], ['escalation_rules.rule_id'], name=op.f('fk_procedures_escalation_rule_id_escalation_rules')),
    sa.PrimaryKeyConstraint('procedure_code', name=op.f('pk_procedures'))
    )
    op.create_table('caller_verifications',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=100), nullable=True),
    sa.Column('caller_role', sa.Enum('PROVIDER_STAFF', 'BROKER', 'SUPPLIER', 'OTHER', name='callerrole', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('organisation_name', sa.String(length=200), nullable=False),
    sa.Column('caller_reference', sa.String(length=60), nullable=False),
    sa.Column('caller_name', sa.String(length=100), nullable=True),
    sa.Column('provider_id', sa.String(length=36), nullable=True),
    sa.Column('member_id', sa.String(length=36), nullable=True),
    sa.Column('authorised', sa.Boolean(), nullable=False),
    sa.Column('failure_code', sa.String(length=60), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_caller_verifications_member_id_members')),
    sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], name=op.f('fk_caller_verifications_provider_id_providers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_caller_verifications'))
    )
    op.create_index(op.f('ix_caller_verifications_conversation_id'), 'caller_verifications', ['conversation_id'], unique=False)

    op.create_table('coverage_terms',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tier_id', sa.String(length=40), nullable=False),
    sa.Column('procedure_code', sa.String(length=40), nullable=False),
    sa.Column('covered', sa.Boolean(), nullable=False),
    sa.Column('pre_authorisation_required', sa.Boolean(), nullable=False),
    sa.Column('member_co_payment_percent', sa.Integer(), nullable=True),
    sa.Column('applicable_sub_limit_aed', sa.Integer(), nullable=True),
    sa.Column('reason_not_covered', sa.Text(), nullable=True),
    sa.Column('source_document', sa.String(length=200), nullable=False),
    sa.Column('source_section', sa.String(length=200), nullable=False),
    sa.ForeignKeyConstraint(['procedure_code'], ['procedures.procedure_code'], name=op.f('fk_coverage_terms_procedure_code_procedures')),
    sa.ForeignKeyConstraint(['tier_id'], ['policy_tiers.tier_id'], name=op.f('fk_coverage_terms_tier_id_policy_tiers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_coverage_terms')),
    sa.UniqueConstraint('tier_id', 'procedure_code', name=op.f('uq_coverage_terms_tier_id_procedure_code'))
    )
    op.create_index(op.f('ix_coverage_terms_procedure_code'), 'coverage_terms', ['procedure_code'], unique=False)
    op.create_index(op.f('ix_coverage_terms_tier_id'), 'coverage_terms', ['tier_id'], unique=False)

    op.create_table('pre_authorization_cases',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_reference', sa.String(length=20), nullable=False),
    sa.Column('status', sa.Enum('RECEIVED', 'INFORMATION_COLLECTION', 'VALIDATION', 'RULE_EVALUATION', 'PENDING_INFORMATION', 'RECOMMENDATION_READY', 'PENDING_HUMAN_REVIEW', 'ESCALATED', 'APPROVED', 'DENIED', 'CLOSED', name='casestatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('created_by_actor_type', sa.Enum('PROVIDER_PORTAL', 'VOICE_AGENT', 'SYSTEM', 'HUMAN_REVIEWER', name='actortype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('created_by_actor_id', sa.String(length=100), nullable=False),
    sa.Column('verification_id', sa.String(length=36), nullable=True),
    sa.Column('caller_name', sa.String(length=100), nullable=True),
    sa.Column('caller_role', sa.Enum('PROVIDER_STAFF', 'BROKER', 'SUPPLIER', 'OTHER', name='callerrole', native_enum=False, create_constraint=True, length=40), nullable=True),
    sa.Column('caller_organisation', sa.String(length=200), nullable=True),
    sa.Column('provider_id', sa.String(length=36), nullable=True),
    sa.Column('member_id', sa.String(length=36), nullable=True),
    sa.Column('procedure_code', sa.String(length=40), nullable=True),
    sa.Column('treatment_date', sa.Date(), nullable=True),
    sa.Column('estimated_cost_aed', sa.Integer(), nullable=True),
    sa.Column('urgency', sa.Enum('STANDARD', 'EXPEDITED', name='urgency', native_enum=False, create_constraint=True, length=40), nullable=True),
    sa.Column('diagnosis_code', sa.String(length=10), nullable=True),
    sa.Column('clinical_summary', sa.Text(), nullable=True),
    sa.Column('review_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('assigned_reviewer_id', sa.String(length=100), nullable=True),
    sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('close_reason', sa.Enum('WITHDRAWN_BY_PROVIDER', 'DECISION_COMMUNICATED', name='closereason', native_enum=False, create_constraint=True, length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint('estimated_cost_aed >= 0', name=op.f('ck_pre_authorization_cases_estimated_cost_non_negative')),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_pre_authorization_cases_member_id_members')),
    sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], name=op.f('fk_pre_authorization_cases_provider_id_providers')),
    sa.ForeignKeyConstraint(['verification_id'], ['caller_verifications.id'], name=op.f('fk_pre_authorization_cases_verification_id_caller_verifications')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pre_authorization_cases')),
    sa.UniqueConstraint('case_reference', name=op.f('uq_pre_authorization_cases_case_reference'))
    )
    op.create_index(op.f('ix_pre_authorization_cases_member_id'), 'pre_authorization_cases', ['member_id'], unique=False)
    op.create_index(op.f('ix_pre_authorization_cases_provider_id'), 'pre_authorization_cases', ['provider_id'], unique=False)
    op.create_index(op.f('ix_pre_authorization_cases_status'), 'pre_authorization_cases', ['status'], unique=False)

    op.create_table('audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.Enum('CASE_CREATED', 'CASE_STATUS_CHANGED', 'INFORMATION_COLLECTED', 'INFORMATION_MODIFIED', 'DOCUMENT_REGISTERED', 'VALIDATION_PASSED', 'VALIDATION_FAILED', 'RULES_EVALUATED', 'RECOMMENDATION_GENERATED', 'HUMAN_REVIEW_REQUESTED', 'CASE_ESCALATED', 'REVIEWER_ASSIGNED', 'HUMAN_DECISION_RECORDED', 'RECOMMENDATION_OVERRIDDEN', 'CASE_CLOSED', 'CALL_SUMMARY_LOGGED', 'HUMAN_CALLBACK_REQUESTED', 'HUMAN_CALLBACK_RESOLVED', 'CALL_RECORDED', name='auditeventtype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('actor_type', sa.Enum('PROVIDER_PORTAL', 'VOICE_AGENT', 'SYSTEM', 'HUMAN_REVIEWER', name='actortype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('actor_id', sa.String(length=100), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('data', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.CheckConstraint('sequence >= 1', name=op.f('ck_audit_events_sequence_positive')),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_audit_events_case_id_pre_authorization_cases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_events')),
    sa.UniqueConstraint('case_id', 'sequence', name=op.f('uq_audit_events_case_id_sequence'))
    )
    op.create_index(op.f('ix_audit_events_case_id'), 'audit_events', ['case_id'], unique=False)

    op.create_table('callback_requests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('reference', sa.String(length=20), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=True),
    sa.Column('conversation_id', sa.String(length=100), nullable=True),
    sa.Column('caller_name', sa.String(length=100), nullable=False),
    sa.Column('caller_organisation', sa.String(length=200), nullable=True),
    sa.Column('caller_role', sa.Enum('PROVIDER_STAFF', 'BROKER', 'SUPPLIER', 'OTHER', name='callerrole', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('callback_phone', sa.String(length=20), nullable=False),
    sa.Column('preferred_language', sa.String(length=5), nullable=False),
    sa.Column('reason', sa.Enum('NON_STANDARD_REQUEST', 'CALLER_REQUESTED_HUMAN', 'SUPPLIER_ENQUIRY', 'URGENT_CLINICAL', 'COMPLAINT', 'UNSUPPORTED_LANGUAGE', 'OTHER', name='callbackreason', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('OPEN', 'RESOLVED', name='callbackstatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by_actor_type', sa.Enum('PROVIDER_PORTAL', 'VOICE_AGENT', 'SYSTEM', 'HUMAN_REVIEWER', name='actortype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('created_by_actor_id', sa.String(length=100), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by', sa.String(length=100), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_callback_requests_case_id_pre_authorization_cases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_callback_requests')),
    sa.UniqueConstraint('reference', name=op.f('uq_callback_requests_reference'))
    )
    op.create_index(op.f('ix_callback_requests_case_id'), 'callback_requests', ['case_id'], unique=False)
    op.create_index(op.f('ix_callback_requests_conversation_id'), 'callback_requests', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_callback_requests_status'), 'callback_requests', ['status'], unique=False)

    op.create_table('case_documents',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('document_type', sa.Enum('REFERRAL_LETTER', 'CLINICAL_NOTES', 'IMAGING_REPORT', 'LAB_RESULTS', 'PRIOR_TREATMENT_RECORD', 'OPERATIVE_PLAN', name='documenttype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('storage_uri', sa.String(length=500), nullable=False),
    sa.Column('media_type', sa.String(length=100), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=True),
    sa.Column('registered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('registered_by_actor_type', sa.Enum('PROVIDER_PORTAL', 'VOICE_AGENT', 'SYSTEM', 'HUMAN_REVIEWER', name='actortype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('registered_by_actor_id', sa.String(length=100), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_case_documents_case_id_pre_authorization_cases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_case_documents'))
    )
    op.create_index(op.f('ix_case_documents_case_id'), 'case_documents', ['case_id'], unique=False)

    op.create_table('rule_evaluations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('engine_name', sa.String(length=80), nullable=False),
    sa.Column('engine_version', sa.String(length=40), nullable=False),
    sa.Column('input_snapshot', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('triggered_by_actor_type', sa.Enum('PROVIDER_PORTAL', 'VOICE_AGENT', 'SYSTEM', 'HUMAN_REVIEWER', name='actortype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('triggered_by_actor_id', sa.String(length=100), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_rule_evaluations_case_id_pre_authorization_cases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rule_evaluations')),
    sa.UniqueConstraint('case_id', 'sequence', name=op.f('uq_rule_evaluations_case_id_sequence'))
    )
    op.create_index(op.f('ix_rule_evaluations_case_id'), 'rule_evaluations', ['case_id'], unique=False)

    op.create_table('voice_tool_invocations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('conversation_id', sa.String(length=100), nullable=False),
    sa.Column('tool_name', sa.String(length=64), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=True),
    sa.Column('succeeded', sa.Boolean(), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('invoked_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_voice_tool_invocations_case_id_pre_authorization_cases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_voice_tool_invocations'))
    )
    op.create_index(op.f('ix_voice_tool_invocations_case_id'), 'voice_tool_invocations', ['case_id'], unique=False)
    op.create_index(op.f('ix_voice_tool_invocations_conversation_id'), 'voice_tool_invocations', ['conversation_id'], unique=False)

    op.create_table('call_logs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('reference', sa.String(length=20), nullable=False),
    sa.Column('conversation_id', sa.String(length=100), nullable=True),
    sa.Column('case_id', sa.String(length=36), nullable=True),
    sa.Column('callback_id', sa.String(length=36), nullable=True),
    sa.Column('caller_role', sa.Enum('PROVIDER_STAFF', 'BROKER', 'SUPPLIER', 'OTHER', name='callerrole', native_enum=False, create_constraint=True, length=40), nullable=True),
    sa.Column('outcome_communicated', sa.String(length=60), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('logged_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['callback_id'], ['callback_requests.id'], name=op.f('fk_call_logs_callback_id_callback_requests')),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_call_logs_case_id_pre_authorization_cases')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_call_logs')),
    sa.UniqueConstraint('reference', name=op.f('uq_call_logs_reference'))
    )
    op.create_index(op.f('ix_call_logs_case_id'), 'call_logs', ['case_id'], unique=False)
    op.create_index(op.f('ix_call_logs_conversation_id'), 'call_logs', ['conversation_id'], unique=False)

    op.create_table('recommendations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('evaluation_id', sa.String(length=36), nullable=False),
    sa.Column('outcome', sa.Enum('RECOMMEND_APPROVAL', 'RECOMMEND_DENIAL', 'REQUEST_MORE_INFORMATION', 'ESCALATE', name='recommendationoutcome', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('determining_rule_ids', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('evidence', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('missing_information', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('sources', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('escalation_citations', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('engine_name', sa.String(length=80), nullable=False),
    sa.Column('engine_version', sa.String(length=40), nullable=False),
    sa.Column('generated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_recommendations_case_id_pre_authorization_cases')),
    sa.ForeignKeyConstraint(['evaluation_id'], ['rule_evaluations.id'], name=op.f('fk_recommendations_evaluation_id_rule_evaluations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recommendations')),
    sa.UniqueConstraint('evaluation_id', name=op.f('uq_recommendations_evaluation_id'))
    )
    op.create_index(op.f('ix_recommendations_case_id'), 'recommendations', ['case_id'], unique=False)

    op.create_table('rule_results',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('evaluation_id', sa.String(length=36), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('rule_id', sa.String(length=80), nullable=False),
    sa.Column('rule_version', sa.String(length=20), nullable=False),
    sa.Column('outcome', sa.Enum('PASS', 'FAIL', 'UNKNOWN', name='ruleoutcome', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=False),
    sa.Column('evidence', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('missing_information', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('sources', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('escalation_rule_id', sa.String(length=20), nullable=True),
    sa.ForeignKeyConstraint(['evaluation_id'], ['rule_evaluations.id'], name=op.f('fk_rule_results_evaluation_id_rule_evaluations')),
    sa.ForeignKeyConstraint(['rule_id', 'rule_version'], ['rule_definitions.rule_id', 'rule_definitions.version'], name=op.f('fk_rule_results_rule_id_rule_definitions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rule_results')),
    sa.UniqueConstraint('evaluation_id', 'rule_id', name=op.f('uq_rule_results_evaluation_id_rule_id'))
    )
    op.create_index(op.f('ix_rule_results_evaluation_id'), 'rule_results', ['evaluation_id'], unique=False)

    op.create_table('review_decisions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('recommendation_id', sa.String(length=36), nullable=False),
    sa.Column('decision', sa.Enum('APPROVE', 'DENY', 'REQUEST_INFORMATION', 'ESCALATE', name='humandecisiontype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('reviewer_id', sa.String(length=100), nullable=False),
    sa.Column('reviewer_role', sa.Enum('CLINICAL_REVIEWER', 'MEDICAL_DIRECTOR', name='reviewerrole', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('is_override', sa.Boolean(), nullable=False),
    sa.Column('from_status', sa.Enum('RECEIVED', 'INFORMATION_COLLECTION', 'VALIDATION', 'RULE_EVALUATION', 'PENDING_INFORMATION', 'RECOMMENDATION_READY', 'PENDING_HUMAN_REVIEW', 'ESCALATED', 'APPROVED', 'DENIED', 'CLOSED', name='from_status_casestatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('to_status', sa.Enum('RECEIVED', 'INFORMATION_COLLECTION', 'VALIDATION', 'RULE_EVALUATION', 'PENDING_INFORMATION', 'RECOMMENDATION_READY', 'PENDING_HUMAN_REVIEW', 'ESCALATED', 'APPROVED', 'DENIED', 'CLOSED', name='to_status_casestatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(decision = 'APPROVE' AND to_status = 'APPROVED') OR (decision = 'DENY' AND to_status = 'DENIED') OR (decision = 'REQUEST_INFORMATION' AND to_status = 'PENDING_INFORMATION') OR (decision = 'ESCALATE' AND to_status = 'ESCALATED')", name=op.f('ck_review_decisions_decision_matches_status')),
    sa.CheckConstraint('length(rationale) > 0', name=op.f('ck_review_decisions_rationale_present')),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_review_decisions_case_id_pre_authorization_cases')),
    sa.ForeignKeyConstraint(['recommendation_id'], ['recommendations.id'], name=op.f('fk_review_decisions_recommendation_id_recommendations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_review_decisions')),
    sa.UniqueConstraint('case_id', 'sequence', name=op.f('uq_review_decisions_case_id_sequence'))
    )
    op.create_index(op.f('ix_review_decisions_case_id'), 'review_decisions', ['case_id'], unique=False)

    _install_append_only_triggers()

def downgrade() -> None:
    _drop_append_only_triggers()
    op.drop_index(op.f('ix_review_decisions_case_id'), table_name='review_decisions')

    op.drop_table('review_decisions')
    op.drop_index(op.f('ix_rule_results_evaluation_id'), table_name='rule_results')

    op.drop_table('rule_results')
    op.drop_index(op.f('ix_recommendations_case_id'), table_name='recommendations')

    op.drop_table('recommendations')
    op.drop_index(op.f('ix_call_logs_conversation_id'), table_name='call_logs')
    op.drop_index(op.f('ix_call_logs_case_id'), table_name='call_logs')

    op.drop_table('call_logs')
    op.drop_index(op.f('ix_voice_tool_invocations_conversation_id'), table_name='voice_tool_invocations')
    op.drop_index(op.f('ix_voice_tool_invocations_case_id'), table_name='voice_tool_invocations')

    op.drop_table('voice_tool_invocations')
    op.drop_index(op.f('ix_rule_evaluations_case_id'), table_name='rule_evaluations')

    op.drop_table('rule_evaluations')
    op.drop_index(op.f('ix_case_documents_case_id'), table_name='case_documents')

    op.drop_table('case_documents')
    op.drop_index(op.f('ix_callback_requests_status'), table_name='callback_requests')
    op.drop_index(op.f('ix_callback_requests_conversation_id'), table_name='callback_requests')
    op.drop_index(op.f('ix_callback_requests_case_id'), table_name='callback_requests')

    op.drop_table('callback_requests')
    op.drop_index(op.f('ix_audit_events_case_id'), table_name='audit_events')

    op.drop_table('audit_events')
    op.drop_index(op.f('ix_pre_authorization_cases_status'), table_name='pre_authorization_cases')
    op.drop_index(op.f('ix_pre_authorization_cases_provider_id'), table_name='pre_authorization_cases')
    op.drop_index(op.f('ix_pre_authorization_cases_member_id'), table_name='pre_authorization_cases')

    op.drop_table('pre_authorization_cases')
    op.drop_index(op.f('ix_coverage_terms_tier_id'), table_name='coverage_terms')
    op.drop_index(op.f('ix_coverage_terms_procedure_code'), table_name='coverage_terms')

    op.drop_table('coverage_terms')
    op.drop_index(op.f('ix_caller_verifications_conversation_id'), table_name='caller_verifications')

    op.drop_table('caller_verifications')
    op.drop_table('procedures')
    op.drop_index(op.f('ix_onboarding_applications_provider_id'), table_name='onboarding_applications')

    op.drop_table('onboarding_applications')
    op.drop_index(op.f('ix_members_tier_id'), table_name='members')

    op.drop_table('members')
    op.drop_table('rule_definitions')
    op.drop_table('providers')
    op.drop_table('policy_tiers')
    op.drop_table('onboarding_requirements')
    op.drop_table('escalation_rules')
    op.drop_table('call_records')
