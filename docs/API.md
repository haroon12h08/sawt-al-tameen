# API Reference

_Generated from the OpenAPI specification by `scripts/export_api_docs.py`. Do not edit by hand._

Backend for provider pre-authorisation requests: case intake, validation, rule evaluation, advisory
recommendations, human review, and an immutable audit trail.

**The system never issues final authorisations or denials.** Only an authorised human reviewer can move a case
to `APPROVED` or `DENIED`.

**Authentication assumption:** requests arrive through a gateway that authenticates the caller and sets
`X-Actor-Type`, `X-Actor-Id`, and (for reviewers) `X-Actor-Roles`. The service must not be exposed directly.

Every response carries `X-Request-ID`. Errors use a uniform envelope: `{"error": {"code", "message", "details",
"request_id", "case_id"}}`.

Schemas referenced below are defined in [`openapi.json`](openapi.json) under `components.schemas`.

## Provider interaction

### `POST /api/v1/cases` — Create a pre-authorisation case

Opens a new case, optionally with initial information. Returns the case with a short, speakable `case_reference` suitable for quoting to a caller.

**Authorisation:** Actor type `PROVIDER_PORTAL` or `VOICE_AGENT`.

**State transitions:** → `RECEIVED`; if information is supplied, `RECEIVED` → `INFORMATION_COLLECTION`.

**Request body:** `CreateCaseCommand`

| Status | Response | Error codes / meaning |
|---|---|---|
| 201 | `CaseView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | INTAKE_ACTOR_REQUIRED |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED / UNKNOWN_PROVIDER / MEMBER_NOT_VERIFIED / UNKNOWN_POLICY / POLICY_MEMBER_MISMATCH / UNKNOWN_PROCEDURE / SERVICE_DATE_IN_PAST |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/by-reference/{case_reference}` — Look up a case by its reference

Resolves a caller-quoted case reference (e.g. `PA-7K3M9Q2B`).

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_reference` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/{case_id}` — Retrieve a case

Returns all collected information, documents, and current status.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `PATCH /api/v1/cases/{case_id}/information` — Submit or update collected information

Partial update: only fields present in the body are applied; explicit nulls are rejected. Identifiers are verified against reference data and any failure rejects the entire update. Records `INFORMATION_COLLECTED` and/or `INFORMATION_MODIFIED` audit events with previous values.

**Authorisation:** Actor type `PROVIDER_PORTAL` or `VOICE_AGENT`.

**State transitions:** `RECEIVED` / `PENDING_INFORMATION` / `RECOMMENDATION_READY` → `INFORMATION_COLLECTION` when anything changes (a changed case must be re-evaluated). Rejected in any other non-editable state.

**Parameters:** `case_id` (path)

**Request body:** `CaseInformationUpdate`

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | INTAKE_ACTOR_REQUIRED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | CASE_NOT_EDITABLE / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED / UNKNOWN_PROVIDER / MEMBER_NOT_VERIFIED / UNKNOWN_POLICY / POLICY_MEMBER_MISMATCH / UNKNOWN_PROCEDURE / SERVICE_DATE_IN_PAST |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/cases/{case_id}/documents` — Register a supporting document

Registers metadata for a document already placed in the document store (`storage_uri`). File upload itself is handled by the document store, not this service.

**Authorisation:** Actor type `PROVIDER_PORTAL` or `VOICE_AGENT`.

**State transitions:** Same as information update: editable states → `INFORMATION_COLLECTION`.

**Parameters:** `case_id` (path)

**Request body:** `RegisterDocumentCommand`

| Status | Response | Error codes / meaning |
|---|---|---|
| 201 | `DocumentView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | INTAKE_ACTOR_REQUIRED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | CASE_NOT_EDITABLE / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/{case_id}/required-information` — Retrieve required and missing information

Lists intake requirements and what is still missing. When intake is complete the ruleset is run as a side-effect-free dry run to surface rule-level needs (e.g. document types). `source` distinguishes information the provider can supply from gaps the insurer must resolve.

**Authorisation:** Any authenticated actor.

**State transitions:** None. No audit event.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `RequiredInformationView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/{case_id}/status` — Retrieve case status

Current status, review queue, statuses reachable from here, and the final human decision if one exists.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseStatusView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/cases/{case_id}/evaluation` — Submit the case for evaluation

Validates intake completeness, evaluates the ruleset, and generates an advisory recommendation, atomically. Incomplete intake is an expected outcome (HTTP 200, `validation_passed=false`), not an error.

**Authorisation:** Actor type `PROVIDER_PORTAL` or `VOICE_AGENT`.

**State transitions:** `INFORMATION_COLLECTION` → `VALIDATION` → either `PENDING_INFORMATION` (intake incomplete) or `RULE_EVALUATION` → `PENDING_INFORMATION` (recommendation `REQUEST_MORE_INFORMATION`) / `RECOMMENDATION_READY` (any other recommendation). Never reaches `APPROVED` or `DENIED`.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `EvaluationResultView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | INTAKE_ACTOR_REQUIRED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | INVALID_STATE_TRANSITION / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/cases/{case_id}/recommendation` — Retrieve the latest recommendation

The most recent system recommendation with its rule results, evidence, rationale, and engine versions. `advisory_only` is always true: a recommendation is not an authorisation decision.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `RecommendationView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 404 | `ErrorResponse` | CASE_NOT_FOUND / RECOMMENDATION_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/cases/{case_id}/human-review-request` — Submit the case for human review

Routes the current recommendation to a human review queue. `ESCALATE` recommendations go to the medical director queue; all others to clinical review.

**Authorisation:** Actor type `PROVIDER_PORTAL`, `VOICE_AGENT`, or `SYSTEM`.

**State transitions:** `RECOMMENDATION_READY` → `PENDING_HUMAN_REVIEW` or `ESCALATED`.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseStatusView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | TRANSITION_NOT_AUTHORIZED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | RECOMMENDATION_NOT_READY / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/cases/{case_id}/closure` — Close a case

`WITHDRAWN_BY_PROVIDER` before review; `DECISION_COMMUNICATED` after a human decision.

**Authorisation:** Withdrawal: `PROVIDER_PORTAL` or `VOICE_AGENT`. After a decision: `SYSTEM` or `HUMAN_REVIEWER`.

**State transitions:** `RECEIVED` / `INFORMATION_COLLECTION` / `PENDING_INFORMATION` / `RECOMMENDATION_READY` / `APPROVED` / `DENIED` → `CLOSED` (terminal).

**Parameters:** `case_id` (path)

**Request body:** `CloseCaseCommand`

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | TRANSITION_NOT_AUTHORIZED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | INVALID_CLOSE_REASON / INVALID_STATE_TRANSITION / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

## Audit

### `GET /api/v1/cases/{case_id}/audit-events` — Retrieve the audit trail

Immutable, ordered audit events for the case. Events are append-only at the database level.

**Authorisation:** Actor type `HUMAN_REVIEWER` or `SYSTEM`.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | array of `AuditEventView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

## Human review

### `GET /api/v1/review/queues/{queue}` — List a review queue

Cases awaiting review in the queue; expedited first, then oldest first.

**Authorisation:** Actor type `HUMAN_REVIEWER`.

**State transitions:** None.

**Parameters:** `queue` (path), `limit` (query)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | array of `ReviewQueueItemView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `GET /api/v1/review/cases/{case_id}` — Retrieve the review packet

Everything a reviewer needs: case details, provider request, collected information, documents, current and historical recommendations with rule results and evidence, prior decisions, and full audit history.

**Authorisation:** Actor type `HUMAN_REVIEWER`.

**State transitions:** None.

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `ReviewPacketView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/review/cases/{case_id}/assignment` — Assign the case to yourself

Self-assignment. A decision can only be recorded by the assigned reviewer. Reassignment is allowed and audited with the previous assignee.

**Authorisation:** Actor type `HUMAN_REVIEWER` with role `CLINICAL_REVIEWER` (case `PENDING_HUMAN_REVIEW`) or `MEDICAL_DIRECTOR` (case `ESCALATED`).

**State transitions:** None (records `REVIEWER_ASSIGNED`).

**Parameters:** `case_id` (path)

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | `CaseStatusView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED / INSUFFICIENT_REVIEWER_ROLE |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | CASE_NOT_UNDER_REVIEW / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/review/cases/{case_id}/decision` — Record a human decision

Records the reviewer's decision against the current recommendation (`recommendation_id` must be the latest; guards against deciding on stale information). A rationale is mandatory. The system recommendation is never modified; departures are flagged `is_override` and audited as `RECOMMENDATION_OVERRIDDEN`.

**Authorisation:** Actor type `HUMAN_REVIEWER` with role `CLINICAL_REVIEWER` (case `PENDING_HUMAN_REVIEW`) or `MEDICAL_DIRECTOR` (case `ESCALATED`). Must be the assigned reviewer.

**State transitions:** `PENDING_HUMAN_REVIEW` → `APPROVED` / `DENIED` / `PENDING_INFORMATION` / `ESCALATED`; `ESCALATED` → `APPROVED` / `DENIED` / `PENDING_INFORMATION`.

**Parameters:** `case_id` (path)

**Request body:** `HumanDecisionCommand`

| Status | Response | Error codes / meaning |
|---|---|---|
| 201 | `ReviewDecisionView` | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | HUMAN_REVIEWER_REQUIRED / INSUFFICIENT_REVIEWER_ROLE / REVIEWER_NOT_ASSIGNED |
| 404 | `ErrorResponse` | CASE_NOT_FOUND |
| 409 | `ErrorResponse` | CASE_NOT_UNDER_REVIEW / STALE_RECOMMENDATION / INVALID_STATE_TRANSITION / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

## Voice agent tools

### `GET /api/v1/agent/tools` — List agent tool definitions

Tool names, descriptions, and JSON Schemas for inputs and outputs, suitable for registering with a tool-calling model. Contains no decision-making tools.

**Authorisation:** Any authenticated actor.

**State transitions:** None.

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | array of JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

### `POST /api/v1/agent/tools/{tool_name}` — Invoke an agent tool

Invokes one tool. The body is the tool's arguments object and is validated strictly against that tool's `input_schema` (see `GET /api/v1/agent/tools`); the response matches its `output_schema`. Errors use the standard envelope and HTTP status of the underlying operation.

**Authorisation:** Actor type `VOICE_AGENT` only.

**State transitions:** Those of the underlying operation (see the corresponding provider-interaction endpoint).

**Parameters:** `tool_name` (path)

**Request body:** JSON object (see tool `input_schema` / `output_schema`)

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |
| 401 | `ErrorResponse` | ACTOR_REQUIRED / INVALID_ACTOR |
| 403 | `ErrorResponse` | VOICE_AGENT_REQUIRED / TRANSITION_NOT_AUTHORIZED |
| 404 | `ErrorResponse` | TOOL_NOT_FOUND / CASE_NOT_FOUND / RECOMMENDATION_NOT_FOUND |
| 409 | `ErrorResponse` | INVALID_STATE_TRANSITION / CASE_NOT_EDITABLE / RECOMMENDATION_NOT_READY / CONCURRENT_MODIFICATION |
| 422 | `ErrorResponse` | REQUEST_VALIDATION_FAILED / TOOL_ARGUMENTS_INVALID / UNKNOWN_PROVIDER / MEMBER_NOT_VERIFIED / ... |
| 500 | `ErrorResponse` | INTERNAL_ERROR / INTEGRITY_VIOLATION |

## Operations

### `GET /health` — Liveness check

**Request body:** —

| Status | Response | Error codes / meaning |
|---|---|---|
| 200 | JSON object (see tool `input_schema` / `output_schema`) | Successful Response |

