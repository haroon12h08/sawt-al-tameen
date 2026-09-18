#!/bin/sh
# Start the whole system locally: no paid API, no cloud database, no telephony provider.
#
# Safe to rerun. Creates the database if it is missing, applies migrations, loads the UAE catalogue on first
# run only, reports what is still missing, then serves the API and the browser console.
set -eu
cd "$(dirname "$0")/.."

export PREAUTH_RUNTIME_MODE=local
export PREAUTH_DATABASE_URL="${PREAUTH_DATABASE_URL:-sqlite:///./preauth.db}"

if [ -f .env ]; then
  # Local configuration only; secrets never leave this machine and .env is git-ignored.
  set -a; . ./.env; set +a
  export PREAUTH_RUNTIME_MODE=local
fi

echo "==> Dependencies"
# --inexact matters: a plain sync prunes whatever is not in the named extras, which would silently uninstall the
# speech engines from `--extra local-voice` every time this script ran.
# Offline operation is a requirement too, so an unreachable index must not stop a local call: if something is
# genuinely missing, the readiness check below names it.
uv sync --extra local --inexact || echo "    (could not reach the package index; continuing with what is installed)"

echo "==> Database: $PREAUTH_DATABASE_URL"
uv run alembic upgrade head
uv run python -m preauth.seed --if-empty --scenarios

# Take the first free port from PREAUTH_LOCAL_PORT (default 8000) upward instead of failing on a busy one.
# Chosen after .env is sourced, so a port set there is honoured.
HOST="${PREAUTH_LOCAL_HOST:-127.0.0.1}"
PORT=$(uv run python scripts/free_port.py "$HOST" "${PREAUTH_LOCAL_PORT:-8000}")
export PREAUTH_LOCAL_PORT="$PORT"
echo "==> Port: $PORT"

echo "==> Local model services"
# Warnings (an uncached Whisper model, a busy port) must not stop a text-only call, so a failure here is
# reported and the server still starts. Run ./scripts/check_local.sh for the full report.
if ! uv run python scripts/check_local.py; then
  echo
  echo "Some checks failed. The server will still start; the browser console reports the same list at"
  echo "http://$HOST:$PORT/api/v1/local/diagnostics"
  echo
fi

echo
echo "==> Sawt Assurance local console:  http://$HOST:$PORT/local"
echo "    API documentation:             http://$HOST:$PORT/docs"
echo "    Text-only agent:               uv run python -m preauth.local_cli"
echo
exec uv run uvicorn preauth.main:app --host "$HOST" --port "$PORT"
