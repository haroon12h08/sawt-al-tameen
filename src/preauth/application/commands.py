"""Input contracts for application use cases. Strict: unknown fields and explicit nulls are rejected."""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from preauth.domain.enums import (
    CallbackReason,
    CallerRole,
    CallOutcome,
    CloseReason,
    DocumentType,
    HumanDecisionType,
    Urgency,
)


def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


def _lower(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


# Identifiers are normalised before their pattern is checked (spoken input arrives in any case).
ProviderNumber = Annotated[str, StringConstraints(pattern=r"^PRV-[0-9]{5}$"), BeforeValidator(_upper)]
PolicyNumber = Annotated[str, StringConstraints(pattern=r"^POL-[A-Z]{2}-[0-9]{4}-[0-9]{6}$"), BeforeValidator(_upper)]
ProcedureCode = Annotated[str, StringConstraints(pattern=r"^SP-[0-9]{5}$"), BeforeValidator(_upper)]
CaseReference = Annotated[str, StringConstraints(pattern=r"^PA-[0-9A-Z]{8}$"), BeforeValidator(_upper)]
CallerReference = Annotated[
    str, StringConstraints(min_length=2, max_length=60), BeforeValidator(_upper)
]
Icd10Code = Annotated[
    str, StringConstraints(pattern=r"^[A-TV-Z][0-9][0-9AB](\.[0-9A-TV-Z]{1,4})?$"), BeforeValidator(_upper)
]
PhoneNumber = Annotated[str, StringConstraints(pattern=r"^\+[1-9][0-9]{7,14}$"), BeforeValidator(_upper)]
LanguageCode = Annotated[str, StringConstraints(pattern=r"^[a-z]{2}$"), BeforeValidator(_lower)]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
OrganisationName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
Uuid = Annotated[
    str, StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class VerifyCallerCommand(StrictModel):
    """Identify the calling organisation, and optionally the member the call is about."""

    caller_role: CallerRole
    organisation_name: OrganisationName
    caller_reference: CallerReference
    caller_name: PersonName | None = None
    member_policy_number: PolicyNumber | None = None
    member_date_of_birth: date | None = None

    @model_validator(mode="after")
    def _member_pair(self) -> "VerifyCallerCommand":
        if (self.member_policy_number is None) != (self.member_date_of_birth is None):
            raise ValueError("member_policy_number and member_date_of_birth must be provided together")
        return self


class CoverageCheckCommand(StrictModel):
    """A complete pre-authorisation request to check against the benefit schedule."""

    verification_id: Uuid
    procedure_code: ProcedureCode
    treatment_date: date
    estimated_cost_aed: Annotated[int, Field(ge=0, le=100_000_000)]
    urgency: Urgency = Urgency.STANDARD
    diagnosis_code: Icd10Code | None = None
    clinical_summary: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None
    # Supplied when re-checking an existing case, e.g. after documents were submitted.
    case_reference: CaseReference | None = None


class LogTranscriptCommand(StrictModel):
    """Record what the caller was told, before any sign-off language is spoken."""

    summary: Annotated[str, StringConstraints(min_length=10, max_length=2000)]
    outcome_communicated: CallOutcome
    verification_id: Uuid | None = None
    case_reference: CaseReference | None = None
    caller_name: PersonName | None = None
    callback_phone: PhoneNumber | None = None
    preferred_language: LanguageCode = "en"
    callback_reason: CallbackReason | None = None


class RegisterDocumentCommand(StrictModel):
    """Registers metadata for a document already stored in the document store."""

    document_type: DocumentType
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    storage_uri: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9+.-]*://\S+$", max_length=500)]
    media_type: Annotated[str, StringConstraints(pattern=r"^[a-z]+/[a-z0-9.+-]+$", max_length=100)]
    content_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None


class HumanDecisionCommand(StrictModel):
    recommendation_id: Uuid
    decision: HumanDecisionType
    rationale: Annotated[str, StringConstraints(min_length=10, max_length=4000)]


class RequestCallbackCommand(StrictModel):
    """Hand a caller to a human: ambiguous, non-rule-based, or out-of-scope requests."""

    case_id: Uuid | None = None
    caller_name: PersonName
    caller_organisation: OrganisationName | None = None
    caller_role: CallerRole
    callback_phone: PhoneNumber
    preferred_language: LanguageCode
    reason: CallbackReason
    summary: Annotated[str, StringConstraints(min_length=10, max_length=2000)]


class ResolveCallbackCommand(StrictModel):
    resolution_note: Annotated[str, StringConstraints(min_length=10, max_length=2000)]


class CloseCaseCommand(StrictModel):
    reason: CloseReason
    note: Annotated[str, StringConstraints(min_length=1, max_length=1000)] | None = None
