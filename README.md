# Pre-Authorisation Case Service

> Multilingual AI voice agent built specifically for health-insurance provider pre-authorisation in the UAE, enabling clinics, brokers, and healthcare providers to submit requests by voice, capture required information, check applicable insurance rules, and prepare recommendations for qualified human approval.

Backend foundation for handling provider pre-authorisation requests at a health insurer. It covers case intake,
validation, rule evaluation, advisory recommendations, human review, and an immutable audit trail. It also exposes
a typed tool boundary for a future voice agent.

**The system never issues final authorisations or denials.** Only an authorised human reviewer can.

- [Architecture](docs/ARCHITECTURE.md): layers, state machine, how decision authority is enforced, rules, audit,
  and known limitations.
- [API reference](docs/API.md), generated from [`docs/openapi.json`](docs/openapi.json).
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)

All data in this repository is synthetic.

## Requirements

Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker is optional, for PostgreSQL.

## Setup

```bash
uv sync --extra postgres
```

## Run tests

```bash
uv run pytest
```

The integration tests build their SQLite databases by running the real Alembic migrations.

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
| `PREAUTH_DATABASE_URL` | `sqlite:///./preauth.db` |
| `PREAUTH_LOG_LEVEL` | `INFO` |

## Regenerate API docs

After changing routes or schemas, run:

```bash
uv run python scripts/export_api_docs.py
```

A test fails if the generated docs are out of date.
