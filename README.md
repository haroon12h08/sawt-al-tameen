<div align="center">

# Sawt al-Tameen

**"The voice of insurance"** — a bilingual voice agent for health-insurance pre-authorisation in the UAE.

Clinics, brokers and suppliers call in. The agent verifies the caller, checks the request against the benefit
schedule, and prepares a recommendation. **A qualified human always makes the decision.**

[![CI](https://github.com/haroon12h08/sawt-al-tameen/actions/workflows/ci.yml/badge.svg)](https://github.com/haroon12h08/sawt-al-tameen/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![PostgreSQL & SQLite](https://img.shields.io/badge/PostgreSQL%20%7C%20SQLite-336791.svg)](https://www.postgresql.org/)
[![Tests](https://img.shields.io/badge/tests-288%20passing-success.svg)](#testing)
[![Synthetic data](https://img.shields.io/badge/data-synthetic-important.svg)](#everything-here-is-fictional)
[![Runs offline](https://img.shields.io/badge/runs-fully%20offline-blueviolet.svg)](#two-ways-to-take-a-call)

English and Arabic · AED throughout · Dubai, Abu Dhabi and Sharjah · DHA and DOH structure

</div>

---

## The rule that shapes the whole system

An automated line must never tell a clinic that treatment is approved or denied. Here that is not a prompt
instruction the model might talk its way around — it is the shape of the software:

- The agent has **three tools**, and none of them can approve, deny or finalise anything.
- Recording a decision is a **reviewer-only API** the voice agent has no credentials for.
- The state machine only lets a `HUMAN_REVIEWER` move a case to `APPROVED` or `DENIED`, and refuses to load if
  that table is ever edited otherwise.
- A case touched by a call **cannot be signed off until the call transcript is on record**.
- A human decision never overwrites the system's recommendation: both are kept, append-only, in the database.

Ask the agent to approve something and it declines, every time, because there is nothing there to call.

---

## How a call works

```
   clinic / broker / supplier
              │  telephone or browser
              ▼
   ┌──────────────────────────┬──────────────────────────┐
   │  ElevenLabs channel      │  local channel           │
   │  Scribe · v3 · Twilio    │  Whisper · Piper · Ollama│
   └────────────┬─────────────┴────────────┬─────────────┘
                │  verify_caller · check_coverage_rule · log_transcript
                ▼
   this backend ── benefit catalogue · rules engine · recommendation · audit trail
                │
                ▼
   review queue  ──▶  clinical reviewer / medical director  ──▶  APPROVED or DENIED
```

| Step | What happens |
|---|---|
| **1. Identify** | Clinic, broker or supplier? Suppliers and patients are routed away from the pre-auth flow. |
| **2. Verify** | Provider number, plus policy number and date of birth. Lapsed policies stop here. |
| **3. Collect** | Procedure code, estimated cost in AED, treatment date — read back digit by digit. |
| **4. Check** | Tier limits, co-payments, network nesting, waiting periods, documents, thresholds. |
| **5. Answer** | A prepared recommendation, a list of missing documents, or an escalation citing the exact rule. |
| **6. Log** | The call is recorded, references are read back, and a human takes it from there. |

---

## The three tools

| Tool | Purpose |
|---|---|
| `verify_caller` | Identifies the organisation and member; returns tier and dependants, or the reason verification failed |
| `check_coverage_rule` | Checks a complete request; prepares a recommendation or escalates with a cited rule |
| `log_transcript` | Records what the caller was told and raises a callback when a human must follow up |

`check_coverage_rule` refuses to run without a verification from `verify_caller`, so no caller reaches a coverage
answer before being identified.

---

## The benefit catalogue

[`knowledge_base/`](knowledge_base/README.md) is the single source of truth. The rules engine decides from it and
the agent retrieves the same files — so when the agent says *"Section 4.14 of the Executive Schedule of
Benefits"*, that heading genuinely exists in a document it can quote.

| Tier | Annual limit | Network | Pre-auth threshold | Outpatient co-pay |
|---|---|---|---|---|
| Basic | AED 150,000 | Basic Network | AED 1,000 | 20% |
| Enhanced | AED 500,000 | Enhanced Network | AED 2,500 | 20% |
| Comprehensive | AED 1,000,000 | Comprehensive Network | AED 5,000 | 10% |
| Executive | AED 3,000,000 | Executive Network (worldwide ex-USA) | AED 10,000 | 0% |

Networks nest: Basic ⊂ Enhanced ⊂ Comprehensive ⊂ Executive. Only Executive covers out-of-network care.

Also inside: **50 procedures** (36 clear, 11 ambiguous, 3 excluded), **20 providers** across three emirates,
**20 members** with dependants and three lapsed policies, an onboarding checklist, and **eight escalation rules**.

### When the rules stop and a person starts

| Rule | Situation |
|---|---|
| `ESC-001` | Required documentation is missing |
| `ESC-002` | Two policy clauses conflict |
| `ESC-003` | The amount exceeds a limit or sub-limit |
| `ESC-004` | Diagnosis or procedure coding is disputed |
| `ESC-005` | The procedure is not in the schedule, or is new technology |
| `ESC-006` | Clinical versus cosmetic intent |
| `ESC-007` | Member eligibility is in doubt (lapse, waiting period) |
| `ESC-008` | Provider is suspended, onboarding or out of network |

An escalation quotes the rule's own words, never a generic "this needs review".

---

## Two ways to take a call

The insurance system is one implementation. The voice provider is an adapter on top of it, and there are two.

| | Local | ElevenLabs |
|---|---|---|
| Speech in / out | faster-whisper / Piper | Scribe v2 / Eleven v3 |
| Language model | Ollama, on your machine | hosted, with cascading fallback |
| Telephony | browser microphone | Twilio |
| Cost | none | account required |
| Works offline | yes, once models are downloaded | no |
| Rules, cases, review, audit | **identical** | **identical** |

A test fails if the local package ever imports the rules engine, the recommendation engine or the case state
machine. There is one of each, and the voice provider does not get its own.

---

## Quickstart

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker is optional, for PostgreSQL.

```bash
uv sync --extra postgres

uv run alembic upgrade head                  # create the schema
uv run python -m preauth.seed --scenarios    # load the catalogue and five demo cases
uv run uvicorn preauth.main:app --reload     # http://localhost:8000/docs
```

Watch five complete calls run against it, with transcripts and assertions:

```bash
uv run python scripts/simulate_conversations.py
```

| Demo case | Outcome |
|---|---|
| MRI brain, documents complete | `RECOMMEND_APPROVAL` → reviewer approves |
| Cholecystectomy, no documents | `REQUEST_MORE_INFORMATION` |
| Knee replacement on the Basic tier | `RECOMMEND_DENIAL` → clinical review |
| Sleeve gastrectomy (ambiguous) | `ESCALATE` → medical director, ESC-001 |
| Cosmetic rhinoplasty | `RECOMMEND_DENIAL` → reviewer overrides to approve |

---

## Talking to it, for free, on your own machine

Nothing paid, nothing hosted, and no Internet once the models are on disk.

```bash
# Dependencies: `local` is enough to type to the agent; add `local-voice` for microphone and speech.
uv sync --extra local --extra local-voice

# Ollama and one tool-calling model  (https://ollama.com/download)
ollama serve &
ollama pull qwen2.5-coder:7b

# A Piper voice, ~60 MB
uv run python -m piper.download_voices en_GB-alba-medium --data-dir ./models/piper
echo 'PREAUTH_LOCAL_TTS_VOICE=./models/piper/en_GB-alba-medium.onnx' >> .env

./scripts/check_local.sh     # says exactly what is still missing, and the command that fixes it
./scripts/run_local.sh       # migrate, seed, serve
```

Then open **http://localhost:8000/local** and press *Start call*. Speak or type; the page shows the transcript,
the tools as they fire, the case reference, the escalation rule and the human-review status side by side.

No microphone, no speakers, or you just want determinism:

```bash
uv run python -m preauth.local_cli
```

Full guide, configuration table, offline notes and troubleshooting: **[docs/LOCAL_MODE.md](docs/LOCAL_MODE.md)**.

### Connecting the ElevenLabs agent

After a one-time Cloudflare tunnel setup (described in [`.env.example`](.env.example)), fill in the HOSTED MODE block
of `.env` and run:

```bash
./scripts/run_hosted.sh
```

It starts the backend and a Cloudflare named tunnel, then runs all 28 deployment checks through the public URL and
stops if any fail. Next it builds the ElevenLabs agent: tools, knowledge base, prompt, Arabic preset and 100
keyterms. It also registers the post-call webhook through the API and imports your Twilio number if you gave one.
Finally it prints the browser test-call link. Ctrl+C stops everything. Details:
[DEPLOYMENT.md → Hosted mode, one command](docs/DEPLOYMENT.md#hosted-mode-one-command).

---

## Testing

```bash
uv run pytest                                             # SQLite
docker compose up -d --wait                               # PostgreSQL
PREAUTH_TEST_DATABASE_URL=postgres://preauth:preauth@localhost:55432/preauth uv run pytest
```

**288 tests**, green on both databases. Integration tests build their schema through the real Alembic migration,
so the migration is tested rather than assumed. Among the things they pin down: all three lapsed members are
rejected and all seventeen active ones accepted; all eleven ambiguous procedures escalate citing their own rule;
missing information is never reported as a failure; no rule result can be mutated after the fact; the audit
trail stays contiguous; and the local agent cannot reach a tool that decides anything, in any mode. The local
tests need no model download — the language model, recogniser and synthesiser are replaced by doubles, so what
they exercise is the agent loop and the guardrails against the real rules and the real database.

| Command | What it proves |
|---|---|
| `scripts/verify_deployment.py` | A live deployment behaves correctly, end to end (28 checks) |
| `scripts/simulate_conversations.py` | Five call shapes, including a caller demanding a decision (30 checks) |
| `scripts/generate_uae_knowledge_base.py --check` | The catalogue is internally consistent |
| `scripts/check_local.sh` | What local mode is still missing on this machine, and how to fix each thing |

---

## Repository layout

```
knowledge_base/          benefit catalogue — the single source of truth
  schedule-*.md            per-tier schedules of benefits (what citations point to)
  escalation_rules.*       ESC-001 … ESC-008, prose and machine-readable
voice/
  system_prompt.md         the hosted agent's instructions
  local_system_prompt.md   the local agent's — shorter, same safety behaviour, tested clause by clause
  agent_tests.json         five dashboard test definitions
src/preauth/
  domain/                  case state machine, review policy, errors — no I/O
  rules/                   the ruleset and its engine (pure)
  recommendation/          rule results → recommendation (pure)
  application/             desk, evaluation, review, callbacks, audit
  infrastructure/          ORM, migration, repositories, logging
  api/                     HTTP routes, schemas, error envelope
  agent_tools/             the three tools and the ElevenLabs adapter
  local/                   the local channel: Ollama, Whisper, Piper, sessions, browser console
  local_cli.py             text-only local agent
scripts/                   setup, verification, simulation, generation, local diagnostics
docs/                      architecture, voice agent, deployment, API
```

---

## Documentation

| Document | Contents |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Layers, state machine, how decision authority is enforced, rules, limitations |
| [Local mode](docs/LOCAL_MODE.md) | Running everything free and offline: install, models, config, offline notes, troubleshooting |
| [Voice agent](docs/VOICE_AGENT.md) | Tools, setup script, workflow nodes, evaluation criteria, tests, terminology |
| [Deployment](docs/DEPLOYMENT.md) | Free hosting, and an honest account of UAE phone numbers |
| [Benefit catalogue](knowledge_base/README.md) | File by file, and how the parts relate |
| [API reference](docs/API.md) | Generated from [`openapi.json`](docs/openapi.json) |
| [Implementation plan](docs/IMPLEMENTATION_PLAN.md) | How the foundation was scoped and built |

## Configuration

| Variable | Default |
|---|---|
| `PREAUTH_RUNTIME_MODE` | `elevenlabs` — `local` also mounts the local channel and console |
| `PREAUTH_DATABASE_URL` | `sqlite:///./preauth.db` (hosted `postgres://` URLs accepted) |
| `PREAUTH_LOG_LEVEL` | `INFO` |
| `PREAUTH_VOICE_AGENT_TOKEN` | unset — voice tools disabled |
| `PREAUTH_GATEWAY_SECRET` | unset — when set, required on staff and reviewer APIs |
| `PREAUTH_ELEVENLABS_WEBHOOK_SECRET` | unset — post-call webhook disabled |
| `PREAUTH_LOCAL_LLM_MODEL` | `qwen2.5-coder:7b` — any tool-calling model Ollama serves |
| `PREAUTH_LOCAL_TTS_VOICE` | unset — path to a Piper `.onnx` voice |

Local mode requires none of `ELEVENLABS_API_KEY`, `PREAUTH_PUBLIC_BASE_URL` or the webhook secret. See
[`.env.example`](.env.example) for the full list and [docs/LOCAL_MODE.md](docs/LOCAL_MODE.md) for what each
one does.

After changing routes, schemas or the catalogue, regenerate the derived files (tests fail otherwise):

```bash
uv run python scripts/export_api_docs.py
uv run python scripts/generate_uae_knowledge_base.py
```

---

## Everything here is fictional

Sawt Assurance is an invented insurer. Every member, provider, policy number, licence number and tariff is
synthetic. Procedure codes use a deliberately fictional `SP-#####` scheme rather than CPT. The structure follows
UAE health-insurance practice — DHA and DOH mandated cover, tiered networks, co-payments, pre-authorisation
thresholds, waiting periods — so the rules behave believably, but **none of it is real policy, and none of it may
be used for a real authorisation decision.**

Built for the Ignyte × ElevenLabs Future of Voice AI Challenge, Banking & Insurance track.
