"""Input contracts for application use cases. Strict: unknown fields and explicit nulls are rejected."""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from preauth.domain.enums import (
    CallbackReason,
    CallerRole,
    CloseReason,
    DocumentType,
    HumanDecisionType,
    PlaceOfService,
    Urgency,
)

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Z0-9][A-Z0-9-]{1,39}$")]
# ICD-10 code with the dot, e.g. M23.221
# E.164 without separators, e.g. +971501234567 or +919812345678
PhoneNumber = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\+[1-9][0-9]{7,14}$")]
# Normalised before the pattern check (StringConstraints applies its pattern before case transforms).
LanguageCode = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z]{2}$"),
    BeforeValidator(lambda v: v.strip().lower() if isinstance(v, str) else v),
]
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Icd10Code = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-TV-Z][0-9][0-9AB](\.[0-9A-TV-Z]{1,4})?$")
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PatientIdentification(StrictModel):
    member_id: Identifier
    date_of_birth: date


class CaseInformationUpdate(StrictModel):
    """Partial update of the information collected for a case. Only supplied fields are applied."""

    provider_number: Identifier | None = None
    patient: PatientIdentification | None = None
    policy_number: Identifier | None = None
    procedure_code: Identifier | None = None
    requested_service_date: date | None = None
    place_of_service: PlaceOfService | None = None
    diagnosis_code: Icd10Code | None = None
    diagnosis_description: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None
    urgency: Urgency | None = None
    conservative_treatment_weeks: Annotated[int, Field(ge=0, le=520)] | None = None
    clinical_summary: Annotated[str, StringConstraints(min_length=1, max_length=4000)] | None = None
    caller_name: PersonName | None = None
    caller_role: CallerRole | None = None

    @model_validator(mode="after")
    def _require_non_null_fields(self) -> "CaseInformationUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one information field must be supplied")
        nulls = sorted(f for f in self.model_fields_set if getattr(self, f) is None)
        if nulls:
            raise ValueError(f"Fields may not be set to null: {nulls}")
        return self


class CreateCaseCommand(StrictModel):
    information: CaseInformationUpdate | None = None


class RegisterDocumentCommand(StrictModel):
    """Registers metadata for a document already stored in the document store."""

    document_type: DocumentType
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    storage_uri: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9+.-]*://\S+$", max_length=500)]
    media_type: Annotated[str, StringConstraints(pattern=r"^[a-z]+/[a-z0-9.+-]+$", max_length=100)]
    content_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")] | None = None


class HumanDecisionCommand(StrictModel):
    recommendation_id: Annotated[str, StringConstraints(min_length=36, max_length=36)]
    decision: HumanDecisionType
    rationale: Annotated[str, StringConstraints(min_length=10, max_length=4000)]


class RequestCallbackCommand(StrictModel):
    """Hand a caller to a human: ambiguous, non-rule-based, or out-of-scope requests."""

    case_id: Annotated[str, StringConstraints(min_length=36, max_length=36)] | None = None
    caller_name: PersonName
    caller_organisation: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
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
