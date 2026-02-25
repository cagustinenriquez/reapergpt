$ErrorActionPreference = "Stop"

$env:REAPERGPT_REAPER_BRIDGE_TRANSPORT = "file"

if (-not $env:REAPERGPT_HOST) { $env:REAPERGPT_HOST = "127.0.0.1" }
if (-not $env:REAPERGPT_PORT) { $env:REAPERGPT_PORT = "8000" }
if (-not $env:REAPERGPT_BRIDGE_DRY_RUN) { $env:REAPERGPT_BRIDGE_DRY_RUN = "false" }

uv run uvicorn companion.main:app --reload --host $env:REAPERGPT_HOST --port $env:REAPERGPT_PORT
