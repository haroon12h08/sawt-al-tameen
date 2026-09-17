# Synthetic UAE pre-authorisation knowledge base

> SYNTHETIC TEST DATA. Fictional insurer, plans, providers, members and procedure codes. Structurally modelled on UAE health-insurance regulation (DHA/DOH) for demonstration only. Not real policy data and not valid for any real authorisation decision.

Demo and test data for a pre-authorisation voice agent: policy tiers, procedure coverage, network providers,
provider onboarding requirements, sample members, and the rules that decide when a case must go to a human.

## Files

| File | Contents | Keys that link it to the others |
|---|---|---|
| `policy_tiers.json` | 4 tiers with annual and sub-limits, networks, co-payments, pre-authorisation thresholds and waiting periods | `tier_id`, `network.network_id` |
| `procedure_coverage.json` | 50 procedures with per-tier cover, pre-authorisation flags, exclusions, waiting periods and escalation reasons | `coverage_by_tier.<tier_id>`, `specialty_required`, `escalation.escalation_rule_id` |
| `network_providers.json` | 20 fictional facilities across Dubai, Abu Dhabi and Sharjah | `networks[]`, `specialties[]`, `status` |
| `supplier_onboarding_requirements.json` | Onboarding checklist and three in-flight applications | `requirement_id`, `provider_id` |
| `sample_members.json` | 20 members with dependants, tiers and policy status | `tier`, `policy_number`, `member_id` |
| `escalation_rules.md` | Plain-language description of what is *not* rule-based | `ESC-001` … `ESC-008` |

## How they fit together

```
sample_members.json ──tier──> policy_tiers.json <──network──> network_providers.json
                                     │                                  │
                          coverage_by_tier / thresholds        specialty_required
                                     ▼                                  │
                         procedure_coverage.json <───────────────────────┘
                                     │
                     escalation.escalation_rule_id
                                     ▼
                           escalation_rules.md
```

A coverage lookup runs: verify the member and read their tier → find the procedure → read `coverage_by_tier` for
that tier → check the amount against the tier threshold and sub-limit → check the provider is active in that tier's
network → if the procedure is `AMBIGUOUS`, or any escalation rule fires, hand to a human.

## Tiers at a glance

| Tier | Annual limit (AED) | Network | Pre-auth threshold (AED) | Outpatient co-pay |
|---|---|---|---|---|
| Basic | 150,000 | Basic Network | 1,000 | 20% |
| Enhanced | 500,000 | Enhanced Network | 2,500 | 20% |
| Comprehensive | 1,000,000 | Comprehensive Network | 5,000 | 10% |
| Executive | 3,000,000 | Executive Network | 10,000 | 0% |

Networks are nested: Basic ⊂ Enhanced ⊂ Comprehensive ⊂ Executive. Only Executive covers out-of-network care.

## Deliberate test cases

Of 50 procedures: **36 clear**, **3 excluded on every tier**, and
**11 ambiguous**. The ambiguous ones exist to trigger escalation rather than a clean
approve/deny, and each names the escalation rule it should raise.

Useful demo calls:
- Clear approval path: MRI knee `SP-10050` for an active Enhanced member at an active provider.
- Missing documentation: biologic therapy `SP-60050` (ESC-001).
- Excluded: cosmetic rhinoplasty `SP-20140`.
- Tier boundary: total knee replacement `SP-20050` for a Basic member (benefit starts at Enhanced).
- Eligibility: any request for a lapsed member (`MBR-2026-0008`, `MBR-2026-0013`, `MBR-2026-0020`) — ESC-007.
- Provider problem: a request from `PRV-30020` (suspended) or `PRV-30016` (onboarding) — ESC-008.

## Loading into the ElevenLabs Knowledge Base

Upload every file in this folder as a knowledge-base document, or run:

```bash
uv run python scripts/elevenlabs_setup.py --include-uae-knowledge-base
```

JSON is uploaded as text, which retrieval handles well for lookups by code or name. `escalation_rules.md` and this
README carry the prose the model reasons over.

## Consistency and regeneration

These files are generated and validated by `scripts/generate_uae_knowledge_base.py`; edit that script, not the
JSON. Validation enforces: per-tier pre-authorisation flags match each procedure's rule and the tier threshold;
excluded procedures never require pre-authorisation; every referenced tier, network and escalation rule exists;
networks nest correctly; every clearly-decidable procedure's specialty is offered by an active provider; amounts
sit within tier limits; and member tiers, policy numbers and dependant records are valid and unique.

```bash
uv run python scripts/generate_uae_knowledge_base.py --check
```

## Relationship to the rest of the repository

This is a **standalone demo catalogue**. The running backend evaluates its own seed data (`PLAN-GOLD-PPO`,
`PLAN-SILVER-HMO`), and `voice/knowledge_base/` is generated from that seed data so the rule citations resolve.
The two describe different fictional product families, so loading both into one agent will produce contradictory
answers about plan names and limits. Pick one: use this catalogue for a UAE-flavoured demo, or the backend-derived
documents for answers that match what the tools actually decide.
