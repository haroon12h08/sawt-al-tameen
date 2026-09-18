# Deployment and phone numbers (zero budget)

> Looking for the free option? You do not have to deploy anything. [Local mode](LOCAL_MODE.md) runs the whole
> system — speech in, speech out, agent, rules, review — on your own machine, with no account, no public URL
> and no telephony provider. This document is for putting the backend where the *hosted* agent can reach it.

The ElevenLabs agent must reach this backend over public HTTPS. The quickest way is the one-command hosted mode
below. The manual options follow it, and after them an honest account of phone numbers.

## Hosted mode, one command

```bash
cp .env.example .env        # once; fill in the HOSTED MODE block
./scripts/run_hosted.sh     # every time
```

| Step | What happens | If it fails |
|---|---|---|
| 0 | Reads `.env`, checks every value is present, and makes one read-only ElevenLabs call to test the API key. Generates the voice-tool token and gateway secret on first run. | Lists every missing value, or says the key was rejected. Nothing is started. |
| a | Migrates the database, loads the catalogue if it is empty, and starts the backend on `PREAUTH_HOSTED_PORT`. | Prints the tail of `.hosted/backend.log`. |
| b | Starts `cloudflared tunnel run` with your token (passed through the environment, not the command line) and waits until `PREAUTH_PUBLIC_BASE_URL/health` answers. | Distinguishes a rejected token, a hostname routed to the wrong port (502) and a hostname that does not resolve. |
| c | Runs `scripts/verify_deployment.py` through the public URL and prints a PASS/FAIL banner. | Stops before touching ElevenLabs, and lists the failed checks. |
| d | Runs `scripts/elevenlabs_setup.py`: secret, three server tools, knowledge base, agent with prompt, Arabic preset and keyterms. Updates in place on re-runs. | Prints ElevenLabs' error body. |
| e | Creates an HMAC workspace webhook for `/api/v1/voice/elevenlabs/post-call` (`POST /v1/workspace/webhooks`), stores the signing secret, and points post-call transcripts at it (`PATCH /v1/convai/settings`). Restarts the backend with the secret and re-verifies, including the webhook checks. | If your workspace already sends webhooks elsewhere, it asks before switching, because the setting is workspace-wide. If ElevenLabs returns no secret, it tells you where to copy it and waits. |
| f | If the three `TWILIO_` values are set, it imports the number (`POST /v1/convai/phone-numbers`) and assigns it to the agent. Otherwise it prints how to get a number, and the run continues. | Prints the Twilio/ElevenLabs error. |
| g | Prints the backend URL, the browser test-call link, the phone number and anything still manual. Keeps running until Ctrl+C. | If the backend or tunnel dies later, it says which one and shows its log. |

A second run changes nothing in ElevenLabs if nothing changed. Webhook, tools, knowledge base and phone assignment
are all reused. Generated secrets live in `.hosted/secrets.env` (git-ignored, mode 600), never in `.env`, so local
mode is unaffected. `--yes` answers the workspace-webhook question in advance. `--no-tunnel` skips `cloudflared`
if `PREAUTH_PUBLIC_BASE_URL` already reaches this machine some other way.

**One-time setup** (full steps in `.env.example`): a domain on Cloudflare, a named tunnel with a published
hostname pointing at `http://localhost:8000`, and `cloudflared` installed. A named tunnel is used rather than a
quick one because its URL survives restarts. A quick tunnel's URL changes every run, which would break the
ElevenLabs tools and webhook each time.

Still configured in the dashboard, because no step above needs them for calls to work: the visual workflow with
per-node tool scoping, evaluation criteria and agent tests ([VOICE_AGENT.md](VOICE_AGENT.md)).

## Generate secrets first (manual options only)

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # run three times
```

| Variable | Used by |
|---|---|
| `PREAUTH_VOICE_AGENT_TOKEN` | ElevenLabs tool calls (the setup script stores it as an ElevenLabs secret) |
| `PREAUTH_GATEWAY_SECRET` | You, when calling staff and reviewer APIs (`X-Gateway-Secret` header) |
| `PREAUTH_ELEVENLABS_WEBHOOK_SECRET` | Post-call webhook. Copy it from ElevenLabs after creating the webhook. |

Never commit these values. `.env` is git-ignored; see `.env.example`.

## Option A: your laptop plus a Cloudflare quick tunnel (free, no account, fastest)

Good for building and testing. Calls only work while your laptop and the tunnel are running, and the URL changes
every time the tunnel restarts.

```bash
# terminal 1: backend
uv sync --extra postgres
export PREAUTH_VOICE_AGENT_TOKEN=... PREAUTH_GATEWAY_SECRET=... PREAUTH_ELEVENLABS_WEBHOOK_SECRET=...
uv run alembic upgrade head
uv run python -m preauth.seed --if-empty      # loads the catalogue from knowledge_base/
uv run uvicorn preauth.main:app --port 8000

# terminal 2: public HTTPS URL
sudo pacman -S cloudflared          # Arch; other systems: github.com/cloudflare/cloudflared/releases
cloudflared tunnel --url http://localhost:8000
# prints https://<random>.trycloudflare.com

# terminal 3: point the agent at it
export ELEVENLABS_API_KEY=... PREAUTH_PUBLIC_BASE_URL=https://<random>.trycloudflare.com
uv run python scripts/elevenlabs_setup.py
```

When the tunnel URL changes, re-run the setup script and update the post-call webhook URL.

## Option B: always on, Hugging Face Spaces plus Neon Postgres (free tiers)

1. **Database.** Create a free project at [neon.tech](https://neon.tech) and copy its connection string
   (`postgresql://...sslmode=require`). The backend converts it to the right driver automatically.
2. **Space.** Create a Space at [huggingface.co/new-space](https://huggingface.co/new-space) with SDK **Docker** and
   visibility **Public**. ElevenLabs must reach it without a Hugging Face login.
3. **Code.** Push this repository to the Space, then copy `deploy/huggingface/README.md` over the Space's
   `README.md`. Hugging Face needs that file's front matter to build the Space as a Docker app on port 7860.
4. **Secrets.** In the Space settings, add `PREAUTH_DATABASE_URL`, `PREAUTH_VOICE_AGENT_TOKEN`,
   `PREAUTH_GATEWAY_SECRET` and `PREAUTH_ELEVENLABS_WEBHOOK_SECRET`. Optionally add `PREAUTH_SEED_SCENARIOS=1` to
   create the five demo cases on first start.
5. **URL.** The public URL is `https://<user>-<space>.hf.space`. Use it as `PREAUTH_PUBLIC_BASE_URL`.
6. **Space README.** Copy `deploy/huggingface/README.md` over the Space's `README.md` for the required front matter.

On start, the container runs migrations, loads synthetic reference data if the database is empty, and serves on
port 7860 (`scripts/start.sh`).

Caveats:
- Free Spaces go to sleep after a period without traffic. The first request after that is slow.
- Before a demo or test call, open `https://<your-space>.hf.space/health` and wait for `{"status":"ok"}`.
- Free-tier limits and sleep policies change. Check the current Hugging Face and Neon terms.

Render's free web services also work with the same Dockerfile. They sleep after about 15 minutes idle, though, and
the cold start can exceed the tools' 20-second timeout.

## Verify a deployment

Before pointing ElevenLabs at the backend, run the same sequence the agent will:

```bash
export PREAUTH_VOICE_AGENT_TOKEN=... PREAUTH_GATEWAY_SECRET=... PREAUTH_ELEVENLABS_WEBHOOK_SECRET=...
uv run python scripts/verify_deployment.py --base-url https://your-backend.example
```

Twenty-eight checks: caller verification (including a lapsed member and a wrong date of birth), supplier
onboarding, the rules and their cited sources, escalation with a cited ESC rule, the guardrails (no decision tool;
coverage blocked without verification; sign-off blocked until the transcript is logged), and the signed post-call
webhook. It closes the case it creates, and exits non-zero if any check fails.

## Phone numbers: what is and isn't free

These are checked facts as of September 2026. Verify them before you rely on them.

### Free today

- **Talk to the agent from your phone's browser.** The ElevenLabs test link
  (`https://elevenlabs.io/app/talk-to?agent_id=...`) runs the exact same agent, tools and backend over the internet.
  There are no call charges, and it works from India.
- **ElevenLabs Free plan:** 15 agent minutes per month and 4 concurrent calls, with no commercial licence. That is
  enough for a handful of test calls, but not for sustained demos. LLM usage is billed separately on paid plans.

### A real phone number (Twilio native integration)

- ElevenLabs imports a Twilio number using your Account SID and Auth Token (Agents → Phone Numbers). Inbound calls
  to that number reach the agent, and no backend change is needed.
- ElevenLabs' integration page describes needing a paid Twilio account and a purchased number. A new Twilio account
  comes with trial credit, but trial accounts carry restrictions. Expect to add a card; a US number costs roughly a
  dollar or two per month plus per-minute charges.
- Calling a US number from an Indian mobile is an international call, charged by your mobile operator.

### A UAE (+971) number

- **Twilio.** Its UAE regulatory guidelines list only **toll-free (+971 800)** numbers. Calls to those must
  originate inside the UAE, so you cannot test one from India. They also need regulatory documents and are paid.
- **Local UAE numbers.** These are issued through UAE-licensed operators (usually via a business SIP provider with
  local presence). They require company KYC and are not free.
- **Any number you obtain.** ElevenLabs can attach it through **SIP trunk** import without code changes.
- **For the challenge.** The brief mentions live test numbers for Stage 2. Ask the Ignyte × ElevenLabs organisers
  whether they provide a UAE test number or telephony credits. That is the realistic zero-budget route to a +971
  number.

### Suggested path

1. Build and test using the browser link, from your laptop and your phone.
2. Record your ElevenLabs Tests pass rates (see [VOICE_AGENT.md](VOICE_AGENT.md)).
3. For a phone demo, either use the organisers' number or spend a few dollars on a Twilio US number; the agent is
   the same.

Sources: [Twilio UAE regulatory guidelines](https://www.twilio.com/en-us/guidelines/ae/regulatory),
[Twilio UAE number terms](https://www.twilio.com/en-us/legal/service-country-specific-terms/uae-phone-numbers),
[ElevenLabs Agents pricing](https://elevenlabs.io/pricing/agents),
[ElevenLabs Twilio native integration](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration),
[Connect Twilio to ElevenLabs](https://elevenlabs.io/agents/integrations/twilio).
