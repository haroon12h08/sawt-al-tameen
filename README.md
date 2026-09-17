# Pre-Authorisation Case Service

> Multilingual AI voice agent built specifically for health-insurance provider pre-authorisation in the UAE, enabling clinics, brokers, and healthcare providers to submit requests by voice, capture required information, check applicable insurance rules, and prepare recommendations for qualified human approval.

Backend foundation for handling provider pre-authorisation requests at a health insurer. It covers case intake,
validation, rule evaluation, advisory recommendations, human review, and an immutable audit trail. It also exposes
a typed tool boundary for a future voice agent.

**The system never issues final authorisations or denials.** Only an authorised human reviewer can.

- [Voice agent (ElevenLabs)](docs/VOICE_AGENT.md): tools, setup script, workflow, evaluation, tests.
- [Deployment and phone numbers](docs/DEPLOYMENT.md): free hosting options, and what is and isn't free for phone
  numbers (including UAE numbers).
- [Architecture](docs/ARCHITECTURE.md): layers, state machine, how decision authority is enforced, rules, audit,
  and known limitations.
- [API reference](docs/API.md), generated from [`docs/openapi.json`](docs/openapi.json).
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

All data in this repository is synthetic.

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
uv run python -m preauth.seed --scenarios
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
| Complete MRI request | `RECOMMEND_APPROVAL` → reviewer approves |
| Arthroscopy without an imaging report | `REQUEST_MORE_INFORMATION` → `PENDING_INFORMATION` |
| Excluded cosmetic procedure | `RECOMMEND_DENIAL` → clinical review queue |
| Procedure with no coverage terms | `ESCALATE` → medical director queue |
| MRI with too little conservative treatment | `RECOMMEND_DENIAL` → reviewer overrides to approve |

## Calling the API

The service expects a gateway to authenticate callers and forward their identity in headers (see
[API reference](docs/API.md)):

```bash
curl -X POST localhost:8000/api/v1/cases \
  -H 'X-Actor-Type: VOICE_AGENT' -H 'X-Actor-Id: voice-1' -H 'content-type: application/json' \
  -d '{"information": {"provider_number": "PRV-100234"}}'

curl localhost:8000/api/v1/review/queues/CLINICAL_REVIEW \
  -H 'X-Actor-Type: HUMAN_REVIEWER' -H 'X-Actor-Id: rev-1' -H 'X-Actor-Roles: CLINICAL_REVIEWER'
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
uv run python scripts/generate_knowledge_base.py
```

Tests fail if the generated API docs or knowledge base are out of date.
