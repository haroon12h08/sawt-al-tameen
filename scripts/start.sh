#!/bin/sh
# Container entry point: migrate, seed synthetic reference data on first start, serve.
set -eu
alembic upgrade head
python -m preauth.seed --if-empty ${PREAUTH_SEED_SCENARIOS:+--scenarios}
exec uvicorn preauth.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips='*'
