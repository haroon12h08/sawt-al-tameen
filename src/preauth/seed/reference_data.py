"""Synthetic reference data.

EVERYTHING HERE IS FICTIONAL. Names, member IDs, policy numbers, provider numbers, procedure codes, and coverage
terms are invented for development and testing. Diagnosis codes are public ICD-10-CM codes, used only as
realistic identifiers. Procedure codes are deliberately synthetic (PROC-*) rather than CPT codes.

Dates are relative to ``today`` so the data remains valid whenever it is loaded.
"""

from datetime import date, timedelta

from sqlalchemy.orm import Session

from preauth.domain.enums import (
    CredentialingStatus,
    DocumentType,
    NetworkStatus,
    PolicyStatus,
    ProviderType,
)
from preauth.infrastructure.clock import new_id
from preauth.infrastructure.db.models import (
    CoverageIndicatedDiagnosis,
    CoverageRequiredDocument,
    CoverageTerm,
    InsurancePlan,
    Patient,
    Policy,
    Procedure,
    Provider,
)

PLANS = [
    ("PLAN-GOLD-PPO", "Gold PPO (synthetic)", True),
    ("PLAN-SILVER-HMO", "Silver HMO (synthetic)", False),
]

PROCEDURES = [
    ("PROC-MRI-KNEE", "MRI of knee without contrast", "IMAGING"),
    ("PROC-KNEE-ARTHROSCOPY", "Arthroscopic partial meniscectomy, knee", "SURGERY"),
    ("PROC-SLEEP-STUDY", "Attended overnight polysomnography", "DIAGNOSTIC"),
    ("PROC-RHINOPLASTY-COSMETIC", "Rhinoplasty, primary, cosmetic", "SURGERY"),
    # Deliberately has no coverage terms on any plan: exercises the insurer-knowledge-gap path.
    ("PROC-GENETIC-PANEL", "Hereditary cancer multigene panel", "LABORATORY"),
]

# procedure -> (covered, preauth_required, required docs, indicated diagnoses, min conservative weeks, annual limit)
COVERAGE = {
    "PROC-MRI-KNEE": (
        True, True, [DocumentType.CLINICAL_NOTES], ["M23.221", "M23.222", "M25.561", "M25.562"], 6, 2,
    ),
    "PROC-KNEE-ARTHROSCOPY": (
        True, True, [DocumentType.CLINICAL_NOTES, DocumentType.IMAGING_REPORT], ["M23.221", "M23.222"], 6, 1,
    ),
    "PROC-SLEEP-STUDY": (True, True, [DocumentType.REFERRAL_LETTER], ["G47.33"], None, 1),
    "PROC-RHINOPLASTY-COSMETIC": (False, False, [], [], None, None),
}

PROVIDERS = [
    ("PRV-100234", "Northgate Orthopaedic Clinic", ProviderType.CLINIC, "Orthopaedics",
     NetworkStatus.IN_NETWORK, CredentialingStatus.ACTIVE),
    ("PRV-100871", "Riverside General Hospital", ProviderType.HOSPITAL, "General surgery",
     NetworkStatus.IN_NETWORK, CredentialingStatus.ACTIVE),
    ("PRV-100990", "Cedar Clinical Genetics", ProviderType.CLINIC, "Clinical genetics",
     NetworkStatus.IN_NETWORK, CredentialingStatus.ACTIVE),
    ("PRV-200415", "Lakeside Sleep Centre", ProviderType.CLINIC, "Sleep medicine",
     NetworkStatus.OUT_OF_NETWORK, CredentialingStatus.ACTIVE),
    ("PRV-300552", "Harbour Day Surgery", ProviderType.CLINIC, "Day surgery",
     NetworkStatus.IN_NETWORK, CredentialingStatus.SUSPENDED),
]

# member_id, given, family, dob, policy_number, plan, policy status
MEMBERS = [
    ("MBR-5001-01", "Nadia", "Farouk", date(1984, 3, 12), "POL-000101", "PLAN-GOLD-PPO", PolicyStatus.ACTIVE),
    ("MBR-5002-01", "Samuel", "Okafor", date(1971, 11, 2), "POL-000102", "PLAN-SILVER-HMO", PolicyStatus.ACTIVE),
    ("MBR-5003-01", "Priya", "Raman", date(1990, 7, 25), "POL-000103", "PLAN-SILVER-HMO", PolicyStatus.ACTIVE),
    ("MBR-5004-01", "Leila", "Mansour", date(1966, 1, 30), "POL-000104", "PLAN-GOLD-PPO", PolicyStatus.ACTIVE),
    ("MBR-5005-01", "Daniel", "Whitfield", date(1979, 9, 9), "POL-000105", "PLAN-GOLD-PPO", PolicyStatus.ACTIVE),
    ("MBR-5006-01", "Tomas", "Lindqvist", date(1958, 5, 17), "POL-000106", "PLAN-SILVER-HMO", PolicyStatus.LAPSED),
]


def load_reference_data(session: Session, today: date) -> None:
    for code, name, oon in PLANS:
        session.add(InsurancePlan(plan_code=code, name=name, out_of_network_covered=oon))
    for code, description, category in PROCEDURES:
        session.add(Procedure(procedure_code=code, description=description, category=category))
    session.flush()

    for plan_code, _, _ in PLANS:
        for procedure_code, (covered, preauth, docs, diagnoses, min_weeks, limit) in COVERAGE.items():
            term = CoverageTerm(
                id=new_id(),
                plan_code=plan_code,
                procedure_code=procedure_code,
                covered=covered,
                preauth_required=preauth,
                min_conservative_treatment_weeks=min_weeks,
                annual_case_limit=limit,
            )
            term.required_documents = [CoverageRequiredDocument(document_type=d) for d in docs]
            term.indicated_diagnoses = [CoverageIndicatedDiagnosis(diagnosis_code=c) for c in diagnoses]
            session.add(term)

    for number, name, ptype, specialty, network, credentialing in PROVIDERS:
        session.add(
            Provider(
                id=new_id(), provider_number=number, name=name, provider_type=ptype, specialty=specialty,
                network_status=network, credentialing_status=credentialing,
            )
        )

    for member_id, given, family, dob, policy_number, plan, status in MEMBERS:
        patient = Patient(id=new_id(), member_id=member_id, given_name=given, family_name=family, date_of_birth=dob)
        session.add(patient)
        session.flush()
        lapsed = status is not PolicyStatus.ACTIVE
        session.add(
            Policy(
                id=new_id(),
                policy_number=policy_number,
                patient_id=patient.id,
                plan_code=plan,
                status=status,
                effective_from=date(today.year - 1, 1, 1),
                effective_to=today - timedelta(days=30) if lapsed else date(today.year + 1, 12, 31),
            )
        )
    session.flush()
