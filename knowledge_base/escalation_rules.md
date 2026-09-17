# Escalation rules

> SYNTHETIC TEST DATA. Fictional insurer, plans, providers, members and procedure codes. Structurally modelled on UAE health-insurance regulation (DHA/DOH) for demonstration only. Not real policy data and not valid for any real authorisation decision.

A pre-authorisation request is **rule-based** when the benefit schedule decides it: the member is verified and
active, the procedure appears in `procedure_coverage.json`, the tier covers it, the documentation required by the
schedule is present, and the amount sits inside the tier's limits. The assistant prepares a recommendation and a
qualified human signs it off.

A request is **not rule-based** when any of the situations below applies. The assistant must then stop preparing a
recommendation and hand the case to a human. It must never approve, deny, or predict the outcome.

| Rule | Situation | What the assistant does |
|---|---|---|
| **ESC-001** | **Missing documentation.** The schedule requires evidence (treatment history, imaging report, clinical notes) that has not been supplied. | Tell the caller exactly which documents are needed and how to upload them. Escalate if they are unavailable. |
| **ESC-002** | **Conflicting policy clauses.** Two clauses of the same policy point to different answers, for example a benefit listed on a tier and also named in the general exclusions. | Escalate to the medical director's queue. Do not choose a clause. |
| **ESC-003** | **Amount exceeds a limit.** The billed amount exceeds the tier's annual limit, the relevant sub-limit, or the member's remaining balance for the policy year. | Escalate with the amount and the limit; a human decides on partial cover. |
| **ESC-004** | **Disputed diagnosis or procedure coding.** The submitted code disagrees with the clinical description, or two codes with different tariffs describe the same treatment. | Escalate for coding review. Do not re-code the request. |
| **ESC-005** | **Procedure not in the coverage list, or new technology.** The procedure has no entry in `procedure_coverage.json`, or is an emerging technique with no tariff line. | Escalate to the medical director. Never infer cover from a similar procedure. |
| **ESC-006** | **Clinical versus cosmetic intent.** Cover depends on whether the procedure is reconstructive or cosmetic, or on an accident that must be evidenced. | Escalate for clinical review. |
| **ESC-007** | **Member eligibility in doubt.** The policy is lapsed or suspended, the treatment falls inside a waiting period, or a pre-existing condition clause may apply. | Tell the caller the request cannot proceed on eligibility grounds and escalate. |
| **ESC-008** | **Provider not active in the network.** The requesting provider is suspended, still onboarding, or outside the member's network, or the call is a supplier or onboarding enquiry. | Escalate to the network department. Onboarding questions are never answered by the pre-authorisation desk. |

## How this maps to the assistant's tools

- The coverage check returns one of three outcomes: a clean recommendation, a request for more information, or
  **escalate**.
- Every ambiguous procedure in `procedure_coverage.json` carries an `escalation.escalation_rule_id` pointing at one
  of the rules above, so the reason given to the reviewer is the same one written here.
- Escalation is a routing decision, not a clinical one. The assistant records the reason and hands over; a
  clinical reviewer or the medical director decides.

## Worked examples

1. **Bariatric sleeve gastrectomy (SP-20110), Comprehensive tier.** Covered on paper, but eligibility rests on BMI,
   supervised weight-management history and comorbidities. Missing evidence of the second and third: **ESC-003 plus
   ESC-001** — escalate.
2. **Robotic-assisted prostatectomy (SP-20130), Enhanced tier.** The tier does not reach this benefit and the
   schedule has no robotic tariff line: **ESC-005** — escalate, do not deny on the tier alone.
3. **Septoplasty (SP-20120) submitted with a rhinoplasty code.** Functional versus cosmetic intent is unclear and
   the coding is disputed: **ESC-006 plus ESC-004** — escalate.
4. **Normal delivery (SP-30020) for a member eight months into the policy.** The 12-month maternity waiting period
   is unsatisfied: **ESC-007** — escalate rather than deny in the call.
5. **MRI knee (SP-10050), Basic tier, AED 2,400.** Covered, above the AED 1,000 threshold, conservative-treatment
   evidence attached: rule-based, so prepare a recommendation for human sign-off.
