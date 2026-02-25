$ErrorActionPreference = "Stop"

if (-not $env:REAPERGPT_HOST) { $env:REAPERGPT_HOST = "127.0.0.1" }
if (-not $env:REAPERGPT_PORT) { $env:REAPERGPT_PORT = "8000" }
if (-not $env:REAPERGPT_BRIDGE_DRY_RUN) { $env:REAPERGPT_BRIDGE_DRY_RUN = "true" }
if (-not $env:REAPERGPT_REAPER_BRIDGE_TRANSPORT) { $env:REAPERGPT_REAPER_BRIDGE_TRANSPORT = "dry_run" }

uv run uvicorn companion.main:app --reload --host $env:REAPERGPT_HOST --port $env:REAPERGPT_PORT
