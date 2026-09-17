# Pre-Authorisation Case Service

> Multilingual AI voice agent built specifically for health-insurance provider pre-authorisation in the UAE, enabling clinics, brokers, and healthcare providers to submit requests by voice, capture required information, check applicable insurance rules, and prepare recommendations for qualified human approval.

The pre-authorisation line for **Sawt Assurance**, a fictional UAE health insurer: a voice agent takes calls from
clinics, brokers and suppliers, checks requests against the benefit catalogue, and prepares recommendations for a
qualified human to sign off. Covers caller verification, rule evaluation with cited sources, escalation with the
specific rule that applies, human review, and an immutable audit trail.

**The system never issues final authorisations or denials.** Only an authorised human reviewer can.

- [Voice agent (ElevenLabs)](docs/VOICE_AGENT.md): the three tools, setup script, workflow, evaluation, tests.
- [Benefit catalogue](knowledge_base/README.md): tiers, procedures, providers, members, escalation rules — the
  single source of truth for every decision.
- [Deployment and phone numbers](docs/DEPLOYMENT.md): free hosting options, and what is and isn't free for phone
  numbers (including UAE numbers).
- [Architecture](docs/ARCHITECTURE.md): layers, state machine, how decision authority is enforced, rules, audit,
  and known limitations.
- [API reference](docs/API.md), generated from [`docs/openapi.json`](docs/openapi.json).
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

All data in this repository is synthetic. `knowledge_base/` is both what the rules decide from and what the agent
retrieves, so a citation the agent reads out always resolves to a real document section.

## What the agent can and cannot do

| Tool | Purpose |
|---|---|
| `verify_caller` | Identify the organisation and the member; lapsed policies are rejected here |
| `check_coverage_rule` | Check a complete request; prepare a recommendation or escalate with a cited rule |
| `log_transcript` | Record what the caller was told; raise a callback when a human must follow up |

There is no tool that approves, denies or finalises a request. Only an assigned human reviewer with the right role
can move a case to `APPROVED` or `DENIED`, and only after the call transcript is on record.

## Voice agent in three steps

1. Run the backend on a public HTTPS URL. The quickest free route is a Cloudflare quick tunnel; see
   [DEPLOYMENT.md](docs/DEPLOYMENT.md).
2. Run `uv run python scripts/elevenlabs_setup.py` with your ElevenLabs API key. It creates the agent, tools,
   knowledge base, Arabic preset and keyterms.
3. Add the post-call webhook in the ElevenLabs dashboard, then talk to the agent from the printed browser link or
   an attached phone number. See [VOICE_AGENT.md](docs/VOICE_AGENT.md).

Check a deployment end to end at any time:

```bash
uv run python scripts/verify_deployment.py --base-url https://your-backend.example
```

## Requirements

Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker is optional, for PostgreSQL.

## Setup

```bash
uv sync --extra postgres
```

## Run tests

```bash
uv run pytest                                             # SQLite
docker compose up -d --wait                               # PostgreSQL
PREAUTH_TEST_DATABASE_URL=postgres://preauth:preauth@localhost:55432/preauth uv run pytest
```

The integration tests build their databases by running the real Alembic migrations. CI runs the suite against both
databases, checks the generated files, and starts the Docker image.

## Run locally

With SQLite (the default URL is `sqlite:///./preauth.db`):

```bash
uv run alembic upgrade head
uv run python -m preauth.seed --scenarios   # loads knowledge_base/ and five demo cases
uv run uvicorn preauth.main:app --reload
```

With PostgreSQL:

```bash
docker compose up -d --wait
export PREAUTH_DATABASE_URL=postgresql+psycopg://preauth:preauth@localhost:55432/preauth
uv run alembic upgrade head
uv run python -m preauth.seed --scenarios
uv run uvicorn preauth.main:app
```

Interactive OpenAPI docs are served at `http://localhost:8000/docs`.

The `--scenarios` flag creates five demonstration cases through the application services, so each has a genuine
audit trail:

| Scenario | Outcome |
|---|---|
| MRI brain with documents complete | `RECOMMEND_APPROVAL` → reviewer approves |
| Cholecystectomy without documents | `REQUEST_MORE_INFORMATION` → `PENDING_INFORMATION` |
| Knee replacement on the Basic tier | `RECOMMEND_DENIAL` → clinical review queue |
| Sleeve gastrectomy (ambiguous, ESC-003) | `ESCALATE` → medical director queue |
| Cosmetic rhinoplasty | `RECOMMEND_DENIAL` → reviewer overrides to approve |

## Calling the API

The service expects a gateway to authenticate callers and forward their identity in headers (see
[API reference](docs/API.md)):

```bash
# what the voice agent does, step one
curl -X POST localhost:8000/api/v1/agent/tools/verify_caller \
  -H 'X-Actor-Type: VOICE_AGENT' -H 'X-Actor-Id: voice-1' -H 'content-type: application/json' \
  -d '{"caller_role": "PROVIDER_STAFF", "organisation_name": "Al Hudaiba Crescent Hospital",
       "caller_reference": "PRV-30011", "member_policy_number": "POL-SA-2026-100001",
       "member_date_of_birth": "1986-04-17"}'

curl localhost:8000/api/v1/review/queues/CLINICAL_REVIEW \
  -H 'X-Actor-Type: HUMAN_REVIEWER' -H 'X-Actor-Id: rev-1' -H 'X-Actor-Roles: CLINICAL_REVIEWER'
```

Walk five complete calls against a running backend, with transcripts:

```bash
uv run python scripts/simulate_conversations.py
```

## Configuration

| Variable | Default |
|---|---|
| `PREAUTH_DATABASE_URL` | `sqlite:///./preauth.db` (hosted `postgres://` URLs are accepted) |
| `PREAUTH_LOG_LEVEL` | `INFO` |
| `PREAUTH_VOICE_AGENT_TOKEN` | unset (voice tools disabled) |
| `PREAUTH_GATEWAY_SECRET` | unset (required on staff and reviewer APIs when set) |
| `PREAUTH_ELEVENLABS_WEBHOOK_SECRET` | unset (post-call webhook disabled) |
| `PREAUTH_SEED_SCENARIOS` | unset (container only: create demo cases on first start) |

## Regenerate API docs

After changing routes or schemas, or the seed coverage data, run:

```bash
uv run python scripts/export_api_docs.py
uv run python scripts/generate_uae_knowledge_base.py
```

Tests fail if the generated API docs or the catalogue are out of date. The catalogue generator also validates
cross-file consistency (thresholds, network nesting, limits, escalation references).
