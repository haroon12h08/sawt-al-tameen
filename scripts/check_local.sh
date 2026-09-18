#!/bin/sh
# Thin wrapper so the diagnostic is one command from a fresh clone.
set -eu
cd "$(dirname "$0")/.."
exec uv run python scripts/check_local.py "$@"
