"""Load the UAE catalogue from knowledge_base/ into the database.

``knowledge_base/`` is the single source of truth: the same files are the agent's knowledge base and, loaded here,
the tables the rules engine reads. Nothing about coverage is defined anywhere else.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from preauth.domain.enums import DirectoryStatus, PolicyStatus
from preauth.infrastructure.clock import new_id
from preauth.infrastructure.db.models import (
    CoverageTerm,
    EscalationRule,
    Member,
    OnboardingApplication,
    OnboardingRequirement,
    PolicyTier,
    Procedure,
    Provider,
)

KNOWLEDGE_BASE = Path(__file__).resolve().parents[3] / "knowledge_base"


def _read(name: str, root: Path) -> dict[str, Any]:
    return json.loads((root / name).read_text())


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def is_loaded(session: Session) -> bool:
    return bool(session.scalar(select(func.count(PolicyTier.tier_id))))


def load_catalogue(session: Session, root: Path = KNOWLEDGE_BASE) -> dict[str, int]:
    tiers_doc = _read("policy_tiers.json", root)
    network_rank = {network_id: rank for rank, network_id in enumerate(tiers_doc["network_hierarchy"], start=1)}

    for rank, tier in enumerate(tiers_doc["tiers"], start=1):
        session.add(
            PolicyTier(
                tier_id=tier["tier_id"],
                name=tier["name"],
                product_name=tier["product_name"],
                tier_rank=rank,
                annual_limit_aed=tier["annual_limit_aed"],
                pre_authorisation_threshold_aed=tier["pre_authorisation_threshold_aed"],
                sub_limits_aed=tier["sub_limits_aed"],
                co_payments_percent=tier["co_payments_percent"],
                co_payment_caps_aed=tier["co_payment_caps_aed"],
                waiting_periods_months=tier["waiting_periods_months"],
                network_id=tier["network"]["network_id"],
                network_name=tier["network"]["name"],
                out_of_network_covered=tier["network"]["out_of_network_cover"],
                source_document=f"{tier['product_name']} Schedule of Benefits 2026",
                notes=tier.get("notes"),
            )
        )

    for rule in _read("escalation_rules.json", root)["rules"]:
        session.add(EscalationRule(**rule))
    session.flush()

    procedures = _read("procedure_coverage.json", root)["procedures"]
    for entry in procedures:
        escalation = entry.get("escalation") or {}
        session.add(
            Procedure(
                procedure_code=entry["code"],
                code_system=entry["code_system"],
                name=entry["name"],
                category=entry["category"],
                specialty_required=entry["specialty_required"],
                typical_billed_amount_aed=entry["typical_billed_amount_aed"],
                pre_authorisation_rule=entry["pre_authorisation_rule"],
                minimum_tier=entry["minimum_tier"],
                waiting_period_months=entry["waiting_period_months"],
                waiting_period_waived_for_emergency=entry["waiting_period_waived_for_emergency"],
                decision_class=entry["decision_class"],
                escalation_rule_id=escalation.get("escalation_rule_id"),
                escalation_reason=escalation.get("reason"),
                exclusions=entry["exclusions"],
                required_documents=entry["required_documents"],
            )
        )
    session.flush()

    for entry in procedures:
        for tier_id, cover in entry["coverage_by_tier"].items():
            session.add(
                CoverageTerm(
                    id=new_id(),
                    tier_id=tier_id,
                    procedure_code=entry["code"],
                    covered=cover["covered"],
                    pre_authorisation_required=cover["pre_authorisation_required"],
                    member_co_payment_percent=cover["member_co_payment_percent"],
                    applicable_sub_limit_aed=cover["applicable_sub_limit_aed"],
                    reason_not_covered=cover["reason_not_covered"],
                    source_document=cover["source_document"],
                    source_section=cover["source_section"],
                )
            )

    providers = _read("network_providers.json", root)["providers"]
    provider_ids: dict[str, str] = {}
    for entry in providers:
        identifier = new_id()
        provider_ids[entry["provider_id"]] = identifier
        session.add(
            Provider(
                id=identifier,
                provider_number=entry["provider_id"],
                name=entry["name"],
                emirate=entry["emirate"],
                area=entry["area"],
                facility_type=entry["facility_type"],
                regulator=entry["regulator"],
                facility_licence_number=entry["facility_licence_number"],
                minimum_network_rank=min(network_rank[n] for n in entry["networks"]),
                specialties=entry["specialties"],
                directory_status=DirectoryStatus(entry["status"].upper().replace("-", "_")),
                notes=entry["notes"],
            )
        )

    for entry in _read("sample_members.json", root)["members"]:
        session.add(
            Member(
                id=new_id(),
                member_id=entry["member_id"],
                policy_number=entry["policy_number"],
                given_name=entry["given_name"],
                family_name=entry["family_name"],
                nationality=entry["nationality"],
                date_of_birth=_date(entry["date_of_birth"]),
                emirates_id=entry["emirates_id"],
                mobile=entry.get("mobile"),
                tier_id=entry["tier"],
                policy_status=PolicyStatus(entry["policy_status"].upper()),
                policy_start_date=_date(entry["policy_start_date"]),
                policy_renewal_date=_date(entry["policy_renewal_date"]),
                policy_lapse_date=_date(entry["policy_lapse_date"]),
                emirate_of_residence=entry["emirate_of_residence"],
                sponsor=entry.get("sponsor"),
                dependents=entry["dependents"],
            )
        )

    onboarding = _read("supplier_onboarding_requirements.json", root)
    for entry in onboarding["checklist"]:
        session.add(
            OnboardingRequirement(
                requirement_id=entry["requirement_id"],
                name=entry["name"],
                issuing_authority=entry["issuing_authority"],
                mandatory=entry["mandatory"],
                validity_months=entry["validity_months"],
                notes=entry["notes"],
            )
        )
    for entry in onboarding["example_applications"]:
        session.add(
            OnboardingApplication(
                application_id=entry["application_id"],
                provider_id=provider_ids[entry["provider_id"]],
                provider_name=entry["provider_name"],
                provider_status=entry["provider_status"],
                documents=entry["documents"],
                outstanding=entry["outstanding"],
            )
        )
    session.flush()
    return {
        "tiers": len(tiers_doc["tiers"]),
        "procedures": len(procedures),
        "providers": len(providers),
        "members": len(_read("sample_members.json", root)["members"]),
    }
