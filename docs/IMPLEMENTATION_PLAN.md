# Pre-Authorisation Infrastructure — Phase 1 Implementation Plan

## 1. Current repository structure

Empty directory (no code, no prior commits). There are no existing conventions to follow.

## 2. Existing technology stack

None in the repository. Available on the development machine: Python 3.12 (pyenv) / 3.14, `uv`,
Node 26, Java 17, Docker, SQLite 3.

**Chosen stack** (smallest set that satisfies the requirements):

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Mature typing, strong ecosystem for later agent/LLM work |
| HTTP API | FastAPI | Native Pydantic validation and OpenAPI generation |
| Schemas | Pydantic v2 (`extra="forbid"`) | Strict request/response validation |
| ORM | SQLAlchemy 2.0 | Portable relational modelling |
| Migrations | Alembic | Standard for SQLAlchemy |
| Database | PostgreSQL (target), SQLite (local/tests) | Same migrations run on both |
| Tests | pytest + httpx TestClient | |
| Logging | stdlib `logging` with a JSON formatter | No extra dependency |

## 3. Proposed architecture

```
src/preauth/
  domain/           Pure domain: enums, case state machine, errors, actors. No I/O.
  rules/            Rules engine abstraction + deterministic mock rule set (pure).
  recommendation/   Recommendation engine: rule results -> recommendation (pure).
  application/      Use-case services: cases, evaluation, human review, audit, queries.
  infrastructure/   DB (ORM models, session, repositories), logging, clock/ids.
  api/              FastAPI routers, request/response schemas, error mapping, actor extraction.
  agent_tools/      Voice-agent boundary: tool registry that calls application services.
  seed/             Synthetic reference data and demonstration scenarios.
migrations/         Alembic migrations.
tests/              unit/ (pure) and integration/ (DB + API).
```

Mapping of the nine requested layers:

| Layer | Location in phase 1 |
|---|---|
| 1. Conversation | *Not built.* Future voice runtime consumes `agent_tools`. |
| 2. Agent/orchestration | `agent_tools` (tool contracts only, no business logic) |
| 3. Case management | `domain/case_state.py`, `application/case_service.py` |
| 4. Provider/patient/request data | `infrastructure/db/models.py` (reference + case tables) |
| 5. Rules/knowledge | `rules/` (engine), `application/rule_context.py` (knowledge retrieval) |
| 6. Decision/recommendation | `recommendation/` |
| 7. Human approval | `application/review_service.py`, `api/routes/review.py` |
| 8. Audit/event logging | `application/audit.py`, `audit_events` table (append-only) |
| 9. External integrations | `infrastructure/` (DB-backed stand-ins; no real integrations) |

Dependency direction: `api`/`agent_tools` → `application` → (`domain`, `rules`, `recommendation`, `infrastructure`).
`domain`, `rules`, and `recommendation` import nothing from the outer layers.

## 4. Domain entities

- **Reference data:** `InsurancePlan`, `Provider`, `Patient`, `Policy`, `Procedure`,
  `CoverageTerm` (plan × procedure), plus the required document types and indicated diagnoses for each coverage term.
- **Case:** `PreAuthorizationCase` (status, urgency, diagnosis, clinical facts, references, version),
  `RequestedService`, `CaseDocument`.
- **Evaluation:** `RuleDefinition` (id+version), `RuleEvaluation` (engine, ruleset version, input snapshot),
  `RuleResult` (PASS / FAIL / UNKNOWN, evidence, missing information, explanation).
- **Recommendation:** `Recommendation` (outcome, rationale, evidence, missing info, engine/version, evaluation link).
- **Human review:** `ReviewDecision` (APPROVE / DENY / REQUEST_INFORMATION / ESCALATE, reviewer, role,
  rationale, reviewed recommendation, `is_override`).
- **Audit:** `AuditEvent` (event id, case id, per-case sequence, type, actor type/id, timestamp, data, request id).

### Case states and transitions

`RECEIVED`, `INFORMATION_COLLECTION`, `VALIDATION`, `RULE_EVALUATION`, `PENDING_INFORMATION`,
`RECOMMENDATION_READY`, `PENDING_HUMAN_REVIEW`, `ESCALATED`, `APPROVED`, `DENIED`, `CLOSED`.

`RECOMMENDATION_READY` is added beyond the example list. Once a recommendation exists, the case must
wait for an explicit "submit for human review" step. Without a separate state, that waiting case would
be mislabelled `RULE_EVALUATION`.

Each transition declares which actor types may perform it. Only a `HUMAN_REVIEWER` actor may move a case
into `APPROVED` or `DENIED`; the state machine enforces this in addition to the service and API checks.

## 5. Database changes

The initial migration creates 17 tables with foreign keys, check constraints, and indexes.
`audit_events`, `rule_evaluations`, `rule_results`, `recommendations`, and `review_decisions` are
**append-only**, enforced with database triggers on both PostgreSQL and SQLite. JSON columns are used only for
evidence, input snapshots, missing-information lists, and audit payloads.

## 6. APIs (`/api/v1`)

Provider interaction: create case, get case, look up case by reference, update information, register document,
missing information, status, submit for evaluation, latest recommendation, request human review, close,
audit history.
Human review: review queue, review packet, assign reviewer, record decision.
Agent boundary: list tool definitions, invoke tool.

Identity comes from gateway-supplied headers (`X-Actor-Type`, `X-Actor-Id`, `X-Actor-Roles`).
**This is a placeholder for real authentication and is not secure on its own.**

## 7. Rules-engine interface

```python
class Rule(Protocol):
    rule_id: str; version: str; description: str; category: RuleCategory
    def evaluate(self, ctx: RuleContext) -> RuleResult: ...

class RulesEngine(Protocol):
    name: str; version: str
    def evaluate(self, ctx: RuleContext) -> EvaluationOutcome: ...
```

`RuleContext` is an immutable, serialisable snapshot (case facts, provider, policy, plan, coverage terms,
documents, prior utilisation, as-of date). It is stored with each evaluation so results can be reproduced.
`RuleResult` enforces invariants: PASS and FAIL carry no missing information, and UNKNOWN must name what is
missing.

## 8. Recommendation-engine interface

```python
class RecommendationEngine(Protocol):
    name: str; version: str
    def recommend(self, results: Sequence[RuleResult], ctx: RuleContext) -> RecommendationDraft: ...
```

Deterministic precedence:
1. Any FAIL → `RECOMMEND_DENIAL`.
2. Otherwise, any UNKNOWN the insurer must resolve (for example, a coverage-knowledge gap) → `ESCALATE`.
3. Otherwise, any UNKNOWN the provider can resolve → `REQUEST_MORE_INFORMATION`.
4. Otherwise, all PASS → `RECOMMEND_APPROVAL`.

## 9. Human-review boundary

- A recommendation is never a decision. `APPROVED` and `DENIED` are reachable only through
  `ReviewService.record_decision` by a `HUMAN_REVIEWER` who holds the right role and is assigned to the case:
  - `CLINICAL_REVIEWER` for the standard queue.
  - `MEDICAL_DIRECTOR` for the escalated queue.
- A decision references the exact recommendation that was reviewed, and that recommendation must be the latest one.
- Decisions and recommendations are separate append-only rows. An override is flagged (`is_override`) and never
  mutates the recommendation.
- The agent tool registry contains no decision tool.

## 10. Testing strategy

- **Unit (pure):** state machine (valid, invalid, actor authority), each rule (PASS/FAIL/UNKNOWN), RuleResult
  invariants, recommendation precedence, reproducibility from a stored snapshot.
- **Integration (SQLite via Alembic migrations):**
  - Case lifecycle and validation.
  - Human approval, override, and escalation.
  - An audit event for every transition.
  - Append-only triggers.
  - API error contract.
  - Agent tools.
  - All five seed scenarios.
  - ORM metadata matches migrations.
- Migrations additionally verified once against PostgreSQL in Docker.

## 11. Implementation phases

1. Scaffold, domain model, and state machine, with unit tests.
2. Rules engine, mock rules, and recommendation engine, with unit tests.
3. Persistence: ORM, migration, append-only triggers, repositories, with tests.
4. Application services: case, evaluation, review, audit, with tests.
5. API, error handling, structured logging, and OpenAPI, with tests.
6. Voice-agent tool boundary, with tests.
7. Synthetic seed data and demonstration scenarios, with tests.
8. API and architecture documentation.
