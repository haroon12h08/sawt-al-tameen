"""initial schema

Creates reference data, case, evaluation, recommendation, review and audit tables, and installs
triggers that make the decision-trail tables append-only on PostgreSQL and SQLite.

Revision ID: 0001
Revises: 
Create Date: 2026-09-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APPEND_ONLY_TABLES = ("rule_evaluations", "rule_results", "recommendations", "review_decisions", "audit_events")


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
    op.create_table('insurance_plans',
    sa.Column('plan_code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('out_of_network_covered', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('plan_code', name=op.f('pk_insurance_plans'))
    )
    op.create_table('patients',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('member_id', sa.String(length=40), nullable=False),
    sa.Column('given_name', sa.String(length=100), nullable=False),
    sa.Column('family_name', sa.String(length=100), nullable=False),
    sa.Column('date_of_birth', sa.Date(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_patients')),
    sa.UniqueConstraint('member_id', name=op.f('uq_patients_member_id'))
    )
    op.create_table('procedures',
    sa.Column('procedure_code', sa.String(length=40), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.PrimaryKeyConstraint('procedure_code', name=op.f('pk_procedures'))
    )
    op.create_table('providers',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('provider_number', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('provider_type', sa.Enum('HOSPITAL', 'CLINIC', name='providertype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('specialty', sa.String(length=100), nullable=False),
    sa.Column('network_status', sa.Enum('IN_NETWORK', 'OUT_OF_NETWORK', name='networkstatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('credentialing_status', sa.Enum('ACTIVE', 'SUSPENDED', 'TERMINATED', name='credentialingstatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_providers')),
    sa.UniqueConstraint('provider_number', name=op.f('uq_providers_provider_number'))
    )
    op.create_table('rule_definitions',
    sa.Column('rule_id', sa.String(length=80), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('category', sa.Enum('ELIGIBILITY', 'COVERAGE', 'MEDICAL_NECESSITY', 'DOCUMENTATION', 'LIMITS', name='rulecategory', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('first_registered_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('rule_id', 'version', name=op.f('pk_rule_definitions'))
    )
    op.create_table('coverage_terms',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('plan_code', sa.String(length=40), nullable=False),
    sa.Column('procedure_code', sa.String(length=40), nullable=False),
    sa.Column('covered', sa.Boolean(), nullable=False),
    sa.Column('preauth_required', sa.Boolean(), nullable=False),
    sa.Column('min_conservative_treatment_weeks', sa.Integer(), nullable=True),
    sa.Column('annual_case_limit', sa.Integer(), nullable=True),
    sa.CheckConstraint('annual_case_limit >= 1', name=op.f('ck_coverage_terms_annual_limit_positive')),
    sa.CheckConstraint('min_conservative_treatment_weeks >= 0', name=op.f('ck_coverage_terms_min_weeks_non_negative')),
    sa.ForeignKeyConstraint(['plan_code'], ['insurance_plans.plan_code'], name=op.f('fk_coverage_terms_plan_code_insurance_plans')),
    sa.ForeignKeyConstraint(['procedure_code'], ['procedures.procedure_code'], name=op.f('fk_coverage_terms_procedure_code_procedures')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_coverage_terms')),
    sa.UniqueConstraint('plan_code', 'procedure_code', name=op.f('uq_coverage_terms_plan_code_procedure_code'))
    )
    op.create_table('policies',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('policy_number', sa.String(length=40), nullable=False),
    sa.Column('patient_id', sa.String(length=36), nullable=False),
    sa.Column('plan_code', sa.String(length=40), nullable=False),
    sa.Column('status', sa.Enum('ACTIVE', 'LAPSED', 'CANCELLED', name='policystatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('effective_to', sa.Date(), nullable=True),
    sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], name=op.f('fk_policies_patient_id_patients')),
    sa.ForeignKeyConstraint(['plan_code'], ['insurance_plans.plan_code'], name=op.f('fk_policies_plan_code_insurance_plans')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_policies')),
    sa.UniqueConstraint('policy_number', name=op.f('uq_policies_policy_number'))
    )
    op.create_index(op.f('ix_policies_patient_id'), 'policies', ['patient_id'], unique=False)

    op.create_table('coverage_indicated_diagnoses',
    sa.Column('coverage_term_id', sa.String(length=36), nullable=False),
    sa.Column('diagnosis_code', sa.String(length=10), nullable=False),
    sa.ForeignKeyConstraint(['coverage_term_id'], ['coverage_terms.id'], name=op.f('fk_coverage_indicated_diagnoses_coverage_term_id_coverage_terms')),
    sa.PrimaryKeyConstraint('coverage_term_id', 'diagnosis_code', name=op.f('pk_coverage_indicated_diagnoses'))
    )
    op.create_table('coverage_required_documents',
    sa.Column('coverage_term_id', sa.String(length=36), nullable=False),
    sa.Column('document_type', sa.Enum('REFERRAL_LETTER', 'CLINICAL_NOTES', 'IMAGING_REPORT', 'LAB_RESULTS', 'PRIOR_TREATMENT_RECORD', 'OPERATIVE_PLAN', name='documenttype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.ForeignKeyConstraint(['coverage_term_id'], ['coverage_terms.id'], name=op.f('fk_coverage_required_documents_coverage_term_id_coverage_terms')),
    sa.PrimaryKeyConstraint('coverage_term_id', 'document_type', name=op.f('pk_coverage_required_documents'))
    )
    op.create_table('pre_authorization_cases',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_reference', sa.String(length=20), nullable=False),
    sa.Column('status', sa.Enum('RECEIVED', 'INFORMATION_COLLECTION', 'VALIDATION', 'RULE_EVALUATION', 'PENDING_INFORMATION', 'RECOMMENDATION_READY', 'PENDING_HUMAN_REVIEW', 'ESCALATED', 'APPROVED', 'DENIED', 'CLOSED', name='casestatus', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('created_by_actor_type', sa.Enum('PROVIDER_PORTAL', 'VOICE_AGENT', 'SYSTEM', 'HUMAN_REVIEWER', name='actortype', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('created_by_actor_id', sa.String(length=100), nullable=False),
    sa.Column('provider_id', sa.String(length=36), nullable=True),
    sa.Column('patient_id', sa.String(length=36), nullable=True),
    sa.Column('policy_id', sa.String(length=36), nullable=True),
    sa.Column('urgency', sa.Enum('STANDARD', 'EXPEDITED', name='urgency', native_enum=False, create_constraint=True, length=40), nullable=True),
    sa.Column('diagnosis_code', sa.String(length=10), nullable=True),
    sa.Column('diagnosis_description', sa.String(length=300), nullable=True),
    sa.Column('conservative_treatment_weeks', sa.Integer(), nullable=True),
    sa.Column('clinical_summary', sa.Text(), nullable=True),
    sa.Column('review_requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('assigned_reviewer_id', sa.String(length=100), nullable=True),
    sa.Column('assigned_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('close_reason', sa.Enum('WITHDRAWN_BY_PROVIDER', 'DECISION_COMMUNICATED', name='closereason', native_enum=False, create_constraint=True, length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.CheckConstraint('conservative_treatment_weeks >= 0', name=op.f('ck_pre_authorization_cases_conservative_weeks_non_negative')),
    sa.ForeignKeyConstraint(['patient_id'], ['patients.id'], name=op.f('fk_pre_authorization_cases_patient_id_patients')),
    sa.ForeignKeyConstraint(['policy_id'], ['policies.id'], name=op.f('fk_pre_authorization_cases_policy_id_policies')),
    sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], name=op.f('fk_pre_authorization_cases_provider_id_providers')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pre_authorization_cases')),
    sa.UniqueConstraint('case_reference', name=op.f('uq_pre_authorization_cases_case_reference'))
    )
    op.create_index(op.f('ix_pre_authorization_cases_patient_id'), 'pre_authorization_cases', ['patient_id'], unique=False)
    op.create_index(op.f('ix_pre_authorization_cases_provider_id'), 'pre_authorization_cases', ['provider_id'], unique=False)
    op.create_index(op.f('ix_pre_authorization_cases_status'), 'pre_authorization_cases', ['status'], unique=False)

    op.create_table('audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.Enum('CASE_CREATED', 'CASE_STATUS_CHANGED', 'INFORMATION_COLLECTED', 'INFORMATION_MODIFIED', 'DOCUMENT_REGISTERED', 'VALIDATION_PASSED', 'VALIDATION_FAILED', 'RULES_EVALUATED', 'RECOMMENDATION_GENERATED', 'HUMAN_REVIEW_REQUESTED', 'CASE_ESCALATED', 'REVIEWER_ASSIGNED', 'HUMAN_DECISION_RECORDED', 'RECOMMENDATION_OVERRIDDEN', 'CASE_CLOSED', name='auditeventtype', native_enum=False, create_constraint=True, length=40), nullable=False),
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

    op.create_table('requested_services',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('procedure_code', sa.String(length=40), nullable=True),
    sa.Column('requested_service_date', sa.Date(), nullable=True),
    sa.Column('place_of_service', sa.Enum('INPATIENT', 'OUTPATIENT', 'OFFICE', name='placeofservice', native_enum=False, create_constraint=True, length=40), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['pre_authorization_cases.id'], name=op.f('fk_requested_services_case_id_pre_authorization_cases')),
    sa.ForeignKeyConstraint(['procedure_code'], ['procedures.procedure_code'], name=op.f('fk_requested_services_procedure_code_procedures')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requested_services')),
    sa.UniqueConstraint('case_id', name=op.f('uq_requested_services_case_id'))
    )
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

    op.create_table('recommendations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('case_id', sa.String(length=36), nullable=False),
    sa.Column('evaluation_id', sa.String(length=36), nullable=False),
    sa.Column('outcome', sa.Enum('RECOMMEND_APPROVAL', 'RECOMMEND_DENIAL', 'REQUEST_MORE_INFORMATION', 'ESCALATE', name='recommendationoutcome', native_enum=False, create_constraint=True, length=40), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=False),
    sa.Column('determining_rule_ids', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('evidence', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
    sa.Column('missing_information', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
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
    op.drop_index(op.f('ix_rule_evaluations_case_id'), table_name='rule_evaluations')

    op.drop_table('rule_evaluations')
    op.drop_table('requested_services')
    op.drop_index(op.f('ix_case_documents_case_id'), table_name='case_documents')

    op.drop_table('case_documents')
    op.drop_index(op.f('ix_audit_events_case_id'), table_name='audit_events')

    op.drop_table('audit_events')
    op.drop_index(op.f('ix_pre_authorization_cases_status'), table_name='pre_authorization_cases')
    op.drop_index(op.f('ix_pre_authorization_cases_provider_id'), table_name='pre_authorization_cases')
    op.drop_index(op.f('ix_pre_authorization_cases_patient_id'), table_name='pre_authorization_cases')

    op.drop_table('pre_authorization_cases')
    op.drop_table('coverage_required_documents')
    op.drop_table('coverage_indicated_diagnoses')
    op.drop_index(op.f('ix_policies_patient_id'), table_name='policies')

    op.drop_table('policies')
    op.drop_table('coverage_terms')
    op.drop_table('rule_definitions')
    op.drop_table('providers')
    op.drop_table('procedures')
    op.drop_table('patients')
    op.drop_table('insurance_plans')
