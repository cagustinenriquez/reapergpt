#!/usr/bin/env sh
set -eu

export REAPERGPT_HOST="${REAPERGPT_HOST:-127.0.0.1}"
export REAPERGPT_PORT="${REAPERGPT_PORT:-8000}"
export REAPERGPT_BRIDGE_DRY_RUN="${REAPERGPT_BRIDGE_DRY_RUN:-true}"

uv run uvicorn companion.main:app --reload --host "$REAPERGPT_HOST" --port "$REAPERGPT_PORT"
