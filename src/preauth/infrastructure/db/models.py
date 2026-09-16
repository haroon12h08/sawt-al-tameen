"""Relational schema.

Reference data (plans, providers, patients, policies, procedures, coverage terms) stands in for systems that a
production insurer would integrate with. Case, evaluation, recommendation, decision, and audit tables are the
system of record for pre-authorisation.

Append-only tables (enforced by database triggers in the migrations): rule_evaluations, rule_results,
recommendations, review_decisions, audit_events.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from preauth.domain.enums import (
    ActorType,
    AuditEventType,
    CaseStatus,
    CloseReason,
    CredentialingStatus,
    DocumentType,
    HumanDecisionType,
    NetworkStatus,
    PlaceOfService,
    PolicyStatus,
    ProviderType,
    RecommendationOutcome,
    ReviewerRole,
    RuleCategory,
    RuleOutcome,
    Urgency,
)
from preauth.infrastructure.db.types import JsonType, UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID = String(36)


def enum_column(enum_cls: type[StrEnum], constraint_name: str | None = None) -> Enum:
    """VARCHAR + CHECK constraint (portable). ``constraint_name`` is needed when a table has two columns of
    the same enum type, because the generated CHECK constraint is named after the enum."""
    return Enum(
        enum_cls,
        name=constraint_name or enum_cls.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=40,
        values_callable=lambda e: [m.value for m in e],
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --------------------------------------------------------------------------- reference data


class InsurancePlan(Base):
    __tablename__ = "insurance_plans"

    plan_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    out_of_network_covered: Mapped[bool] = mapped_column(Boolean)


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    provider_number: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    provider_type: Mapped[ProviderType] = mapped_column(enum_column(ProviderType))
    specialty: Mapped[str] = mapped_column(String(100))
    network_status: Mapped[NetworkStatus] = mapped_column(enum_column(NetworkStatus))
    credentialing_status: Mapped[CredentialingStatus] = mapped_column(enum_column(CredentialingStatus))


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    member_id: Mapped[str] = mapped_column(String(40), unique=True)
    given_name: Mapped[str] = mapped_column(String(100))
    family_name: Mapped[str] = mapped_column(String(100))
    date_of_birth: Mapped[date] = mapped_column(Date)


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    policy_number: Mapped[str] = mapped_column(String(40), unique=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("insurance_plans.plan_code"))
    status: Mapped[PolicyStatus] = mapped_column(enum_column(PolicyStatus))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)

    plan: Mapped[InsurancePlan] = relationship()
    patient: Mapped[Patient] = relationship()


class Procedure(Base):
    __tablename__ = "procedures"

    procedure_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    description: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(60))


class CoverageTerm(Base):
    """Plan-specific coverage terms for a procedure. This is the knowledge the rules consult."""

    __tablename__ = "coverage_terms"
    __table_args__ = (
        UniqueConstraint("plan_code", "procedure_code"),
        CheckConstraint("min_conservative_treatment_weeks >= 0", name="min_weeks_non_negative"),
        CheckConstraint("annual_case_limit >= 1", name="annual_limit_positive"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("insurance_plans.plan_code"))
    procedure_code: Mapped[str] = mapped_column(ForeignKey("procedures.procedure_code"))
    covered: Mapped[bool] = mapped_column(Boolean)
    preauth_required: Mapped[bool] = mapped_column(Boolean)
    min_conservative_treatment_weeks: Mapped[int | None] = mapped_column(Integer)
    annual_case_limit: Mapped[int | None] = mapped_column(Integer)

    required_documents: Mapped[list["CoverageRequiredDocument"]] = relationship(
        order_by="CoverageRequiredDocument.document_type"
    )
    indicated_diagnoses: Mapped[list["CoverageIndicatedDiagnosis"]] = relationship(
        order_by="CoverageIndicatedDiagnosis.diagnosis_code"
    )


class CoverageRequiredDocument(Base):
    __tablename__ = "coverage_required_documents"

    coverage_term_id: Mapped[str] = mapped_column(ForeignKey("coverage_terms.id"), primary_key=True)
    document_type: Mapped[DocumentType] = mapped_column(enum_column(DocumentType), primary_key=True)


class CoverageIndicatedDiagnosis(Base):
    __tablename__ = "coverage_indicated_diagnoses"

    coverage_term_id: Mapped[str] = mapped_column(ForeignKey("coverage_terms.id"), primary_key=True)
    diagnosis_code: Mapped[str] = mapped_column(String(10), primary_key=True)


class RuleDefinition(Base):
    __tablename__ = "rule_definitions"

    rule_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[RuleCategory] = mapped_column(enum_column(RuleCategory))
    first_registered_at: Mapped[datetime] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------- cases


class PreAuthorizationCase(Base):
    __tablename__ = "pre_authorization_cases"
    __table_args__ = (
        CheckConstraint("conservative_treatment_weeks >= 0", name="conservative_weeks_non_negative"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_reference: Mapped[str] = mapped_column(String(20), unique=True)
    status: Mapped[CaseStatus] = mapped_column(enum_column(CaseStatus), index=True)
    created_by_actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    created_by_actor_id: Mapped[str] = mapped_column(String(100))

    provider_id: Mapped[str | None] = mapped_column(ForeignKey("providers.id"), index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), index=True)
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("policies.id"))
    urgency: Mapped[Urgency | None] = mapped_column(enum_column(Urgency))
    diagnosis_code: Mapped[str | None] = mapped_column(String(10))
    diagnosis_description: Mapped[str | None] = mapped_column(String(300))
    conservative_treatment_weeks: Mapped[int | None] = mapped_column(Integer)
    clinical_summary: Mapped[str | None] = mapped_column(Text)

    review_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    assigned_reviewer_id: Mapped[str | None] = mapped_column(String(100))
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    close_reason: Mapped[CloseReason | None] = mapped_column(enum_column(CloseReason))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    version: Mapped[int] = mapped_column(Integer)

    __mapper_args__ = {"version_id_col": version}

    provider: Mapped[Provider | None] = relationship()
    patient: Mapped[Patient | None] = relationship()
    policy: Mapped[Policy | None] = relationship()
    requested_service: Mapped["RequestedService"] = relationship(back_populates="case")
    documents: Mapped[list["CaseDocument"]] = relationship(order_by="CaseDocument.registered_at")


class RequestedService(Base):
    """The service requested on a case. Phase 1 supports exactly one service per case (unique case_id)."""

    __tablename__ = "requested_services"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), unique=True)
    procedure_code: Mapped[str | None] = mapped_column(ForeignKey("procedures.procedure_code"))
    requested_service_date: Mapped[date | None] = mapped_column(Date)
    place_of_service: Mapped[PlaceOfService | None] = mapped_column(enum_column(PlaceOfService))

    case: Mapped[PreAuthorizationCase] = relationship(back_populates="requested_service")
    procedure: Mapped[Procedure | None] = relationship()


class CaseDocument(Base):
    """Metadata for supporting documentation. Content lives in an external document store."""

    __tablename__ = "case_documents"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    document_type: Mapped[DocumentType] = mapped_column(enum_column(DocumentType))
    title: Mapped[str] = mapped_column(String(200))
    storage_uri: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(100))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime)
    registered_by_actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    registered_by_actor_id: Mapped[str] = mapped_column(String(100))


# --------------------------------------------------------------------------- evaluation & recommendation


class RuleEvaluation(Base):
    __tablename__ = "rule_evaluations"
    __table_args__ = (UniqueConstraint("case_id", "sequence"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    engine_name: Mapped[str] = mapped_column(String(80))
    engine_version: Mapped[str] = mapped_column(String(40))
    input_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonType)
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    triggered_by_actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    triggered_by_actor_id: Mapped[str] = mapped_column(String(100))

    results: Mapped[list["RuleResultRecord"]] = relationship(order_by="RuleResultRecord.position")


class RuleResultRecord(Base):
    __tablename__ = "rule_results"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "rule_id"),
        ForeignKeyConstraint(
            ["rule_id", "rule_version"], ["rule_definitions.rule_id", "rule_definitions.version"]
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("rule_evaluations.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(80))
    rule_version: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[RuleOutcome] = mapped_column(enum_column(RuleOutcome))
    explanation: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JsonType)
    missing_information: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("rule_evaluations.id"), unique=True)
    outcome: Mapped[RecommendationOutcome] = mapped_column(enum_column(RecommendationOutcome))
    rationale: Mapped[str] = mapped_column(Text)
    determining_rule_ids: Mapped[list[str]] = mapped_column(JsonType)
    evidence: Mapped[dict[str, Any]] = mapped_column(JsonType)
    missing_information: Mapped[list[dict[str, Any]]] = mapped_column(JsonType)
    engine_name: Mapped[str] = mapped_column(String(80))
    engine_version: Mapped[str] = mapped_column(String(40))
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    evaluation: Mapped[RuleEvaluation] = relationship()


# --------------------------------------------------------------------------- human review


class ReviewDecision(Base):
    """A human reviewer's decision. Never modifies the recommendation it responds to."""

    __tablename__ = "review_decisions"
    __table_args__ = (
        # A decision's resulting status is fixed by its type; the database refuses inconsistent rows.
        CheckConstraint(
            "(decision = 'APPROVE' AND to_status = 'APPROVED')"
            " OR (decision = 'DENY' AND to_status = 'DENIED')"
            " OR (decision = 'REQUEST_INFORMATION' AND to_status = 'PENDING_INFORMATION')"
            " OR (decision = 'ESCALATE' AND to_status = 'ESCALATED')",
            name="decision_matches_status",
        ),
        CheckConstraint("length(rationale) > 0", name="rationale_present"),
        UniqueConstraint("case_id", "sequence"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"))
    decision: Mapped[HumanDecisionType] = mapped_column(enum_column(HumanDecisionType))
    rationale: Mapped[str] = mapped_column(Text)
    reviewer_id: Mapped[str] = mapped_column(String(100))
    reviewer_role: Mapped[ReviewerRole] = mapped_column(enum_column(ReviewerRole))
    is_override: Mapped[bool] = mapped_column(Boolean)
    from_status: Mapped[CaseStatus] = mapped_column(enum_column(CaseStatus, "from_status_casestatus"))
    to_status: Mapped[CaseStatus] = mapped_column(enum_column(CaseStatus, "to_status_casestatus"))
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime)


# --------------------------------------------------------------------------- audit


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("case_id", "sequence"),
        CheckConstraint("sequence >= 1", name="sequence_positive"),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("pre_authorization_cases.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[AuditEventType] = mapped_column(enum_column(AuditEventType))
    actor_type: Mapped[ActorType] = mapped_column(enum_column(ActorType))
    actor_id: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    request_id: Mapped[str | None] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(JsonType)


APPEND_ONLY_TABLES = ("rule_evaluations", "rule_results", "recommendations", "review_decisions", "audit_events")
