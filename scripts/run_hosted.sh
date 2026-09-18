#!/bin/sh
# Hosted (ElevenLabs) mode, one command: backend -> Cloudflare named tunnel -> verification -> ElevenLabs agent
# -> post-call webhook -> phone number -> summary. Reads .env. Ctrl+C stops everything it started.
# Every step is in scripts/hosted.py; see the one-time setup in .env.example.
set -eu
cd "$(dirname "$0")/.."

# --inexact: never uninstall extras another mode installed. Offline, carry on with what is installed.
uv sync --extra postgres --inexact >/dev/null 2>&1 || echo "(could not reach the package index; continuing)"
exec uv run python scripts/hosted.py "$@"
