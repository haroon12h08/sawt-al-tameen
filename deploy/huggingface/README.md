---
title: Sawt Al Tameen
emoji: 📞
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Sawt Al Tameen — pre-authorisation backend

Backend for a provider pre-authorisation voice agent. See the
[source repository](https://github.com/haroon12h08/sawt-al-tameen) for documentation.

**Copy this file to the root of your Hugging Face Space repository as `README.md`.** Hugging Face requires the
front matter above to build the Space as a Docker app on port 7860.

Set these as Space secrets (Settings → Variables and secrets):

| Secret | Purpose |
|---|---|
| `PREAUTH_DATABASE_URL` | Hosted Postgres URL (e.g. Neon). Without it the database is wiped on restart. |
| `PREAUTH_VOICE_AGENT_TOKEN` | Bearer token for the ElevenLabs tool calls |
| `PREAUTH_GATEWAY_SECRET` | Required on staff and reviewer APIs |
| `PREAUTH_ELEVENLABS_WEBHOOK_SECRET` | Post-call webhook signing secret |
| `PREAUTH_SEED_SCENARIOS` | Optional: set to `1` to create the demo cases on first start |

The Space must be **public** so ElevenLabs can reach the webhook and tool endpoints.
