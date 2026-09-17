"""Generate the voice agent's knowledge-base documents from the seed reference data.

The rules engine cites "<plan document>, Section 4.n ..." for every coverage decision. Generating the knowledge base
from the same data guarantees those citations resolve to real sections in the documents the agent can retrieve.

    uv run python scripts/generate_knowledge_base.py          # write voice/knowledge_base/*.md
    uv run python scripts/generate_knowledge_base.py --check  # exit 1 if stale

Member and patient records are deliberately NOT included: the agent must verify members through tools, never
from retrieved text.
"""

import sys
from pathlib import Path

from preauth.seed.reference_data import (
    COVERAGE,
    PLANS,
    PROCEDURES,
    PROVIDERS,
    coverage_section,
    plan_document_name,
)

OUT = Path(__file__).resolve().parents[1] / "voice" / "knowledge_base"

HEADER = (
    "> SYNTHETIC DOCUMENT for development and demonstration. It is not real insurance policy and must not be used "
    "for real authorisation decisions.\n"
)


def _slug(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "-" for c in text).strip("-").replace("--", "-")


def plan_document(plan_code: str, plan_name: str, oon_covered: bool) -> str:
    lines = [
        f"# {plan_document_name(plan_code)}",
        "",
        HEADER,
        f"Plan code: {plan_code}",
        "",
        "## Section 1 General",
        "",
        "1.1 Pre-authorisation decisions are made only by a qualified clinical reviewer or medical director employed "
        "by the insurer. Intake staff and automated assistants collect information and prepare recommendations; they "
        "do not approve or deny requests.",
        "",
        "1.2 A request is processed once the requesting provider, the verified member, the policy, the procedure, "
        "the planned date and place of service, the primary diagnosis (ICD-10) and the urgency are known.",
        "",
        "## Section 2 Eligibility",
        "",
        "2.1 The member's policy must be active on the planned date of service.",
        "",
        "2.2 The requesting provider must hold active credentialing with the insurer.",
        "",
        "2.3 " + (
            "Out-of-network providers are covered under this plan, subject to the same criteria."
            if oon_covered
            else "Out-of-network providers are not covered under this plan."
        ),
        "",
        "## Section 3 Supporting documents",
        "",
        "3.1 Supporting documents are submitted through the provider portal, quoting the case reference. Documents "
        "cannot be accepted by telephone.",
        "",
        "## Section 4 Procedure schedule",
        "",
    ]
    for code, description, category in PROCEDURES:
        lines.append(f"### {coverage_section(code)}")
        lines.append("")
        lines.append(f"Category: {category}.")
        terms = COVERAGE.get(code)
        if terms is None:
            lines.append(
                "No standard coverage terms are published for this procedure. Requests are referred to the "
                "medical director for individual consideration."
            )
            lines.append("")
            continue
        covered, preauth, docs, diagnoses, min_weeks, limit = terms
        if not covered:
            lines.append("This procedure is excluded from the plan and is not a covered benefit.")
            lines.append("")
            continue
        lines.append(f"Covered benefit. Pre-authorisation required: {'yes' if preauth else 'no'}.")
        if diagnoses:
            lines.append("Accepted indications (ICD-10): " + ", ".join(diagnoses) + ".")
        if docs:
            lines.append("Required supporting documents: " + ", ".join(d.value for d in docs) + ".")
        if min_weeks:
            lines.append(f"Minimum conservative treatment before the request: {min_weeks} weeks.")
        if limit:
            lines.append(f"Annual limit: {limit} approved request(s) per member per service year.")
        lines.append("")
    return "\n".join(lines)


def provider_register() -> str:
    lines = [
        "# Provider network and credentialing register",
        "",
        HEADER,
        "Status of providers that may submit pre-authorisation requests.",
        "",
        "| Provider number | Name | Type | Specialty | Network | Credentialing |",
        "|---|---|---|---|---|---|",
    ]
    for number, name, ptype, specialty, network, credentialing in PROVIDERS:
        lines.append(
            f"| {number} | {name} | {ptype.value} | {specialty} | {network.value} | {credentialing.value} |"
        )
    return "\n".join(lines) + "\n"


def process_guide() -> str:
    return f"""# Pre-authorisation desk: caller guide

{HEADER}
## Who can submit a request

Staff of a clinic or hospital, or a broker acting for a provider, can submit a pre-authorisation request. Suppliers
and other callers are handed to a staff member who will call back.

## What the caller needs

- Provider number (PRV-######)
- Member ID (MBR-####-##) and the member's date of birth
- Policy number (POL-######)
- Procedure code, planned date of service, place of service (inpatient, outpatient or office)
- Primary diagnosis as an ICD-10 code
- Whether the request is standard or clinically urgent (expedited)
- For some procedures: weeks of conservative treatment already completed

## After the call

- The caller receives a case reference (PA- followed by 8 characters).
- Supporting documents are uploaded through the provider portal, quoting the case reference.
- When the request is complete it is reviewed by a qualified clinical reviewer. Requests that fall outside the
  standard criteria are reviewed by the medical director.
- The decision is communicated to the provider by the insurer after human review. The telephone assistant never
  gives a decision.

## Languages

Calls are handled in English and Arabic. Callers who prefer another language (for example Hindi or Urdu) can ask for a
staff member to call them back in that language.
"""


def documents() -> dict[str, str]:
    docs = {f"{_slug(code)}.md": plan_document(code, name, oon) for code, name, oon in PLANS}
    docs["provider-network-register.md"] = provider_register()
    docs["preauthorisation-caller-guide.md"] = process_guide()
    return docs


def main() -> int:
    rendered = {OUT / name: content for name, content in documents().items()}
    if "--check" in sys.argv:
        stale = [p.name for p, c in rendered.items() if not p.exists() or p.read_text() != c]
        if stale:
            print(f"Stale knowledge base: {stale}. Run scripts/generate_knowledge_base.py", file=sys.stderr)
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for path, content in rendered.items():
        path.write_text(content)
        print(f"wrote {path.relative_to(OUT.parents[1])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
