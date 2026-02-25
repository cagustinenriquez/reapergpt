# ReaperGPT (MVP Scaffold)

Standalone AI DAW companion targeting REAPER.

## Current MVP scope

- FastAPI backend with `GET /health`
- `POST /actions` endpoint for structured REAPER actions
- `POST /prompt` endpoint (free-text prompt -> planned actions -> dispatch)
- Strict action schema validation (Pydantic)
- REAPER bridge communication via dry-run, HTTP stub, or local file bridge
- REAPER Lua file bridge for real `transport.play` / `transport.stop`

## Not implemented yet

- LLM planning/composition logic
- Audio analysis pipeline
- WebSocket streaming
- OSC transport
- Most REAPER action execution beyond transport play/stop

## Run (dev)

1. Install `uv` (if needed): `pip install uv`
2. Create env + install deps: `uv sync --dev`
3. Start server: `scripts/run_dev.ps1` (Windows) or `sh scripts/run_dev.sh`

## Run tests

- `uv run pytest -q`

## Example request

`POST /actions`

```json
{
  "batch": {
    "actions": [
      { "type": "transport.play", "params": {} },
      { "type": "project.set_tempo", "params": { "bpm": 128 } }
    ]
  }
}
```

## Use with REAPER (MVP file bridge)

1. Install `ReaImGui` in REAPER (via ReaPack), if not already installed.
2. In REAPER, load and run `reaper_bridge/reapergpt_panel.lua` from the Action List.
3. Keep that script running (it opens the ReaperGPT panel and polls for commands).
4. Start the companion API with file bridge mode:

```powershell
$env:REAPERGPT_REAPER_BRIDGE_TRANSPORT = "file"
uv run uvicorn companion.main:app --reload
```

5. Send an action:

```json
{
  "batch": {
    "actions": [
      { "type": "transport.play", "params": {} }
    ]
  }
}
```

Note: Python file bridge MVP currently supports `transport.play`, `transport.stop`, `project.set_tempo`, `regions.create_song_form`, `track.select`, `track.mute`, `track.solo`, and `track.record_arm`.
The REAPER panel also supports local typed commands: `play`, `stop`, `tempo 128`, `select track 1`, `solo track 2`, `mute track 3`, `record arm track 1`, `create regions for a pop song`.

## Free Local Model (Ollama)

You can run a free local model and use `/prompt` for natural-language commands.

1. Install Python dependencies (first time only):

```powershell
uv sync --dev
```

2. Install Ollama, start the Ollama app/service, and pull a model (example):

```powershell
ollama pull qwen2.5:7b-instruct
```

3. Open PowerShell in this repo (`c:\Users\cenriquez\Desktop\reapergpt`) and start the companion with file bridge + Ollama:

```powershell
$env:REAPERGPT_REAPER_BRIDGE_TRANSPORT = "file"
$env:REAPERGPT_LLM_PROVIDER = "ollama"
$env:REAPERGPT_OLLAMA_MODEL = "qwen2.5:7b-instruct"
.\scripts\run_dev.ps1
```

4. In a second PowerShell window, send a free-text prompt:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/prompt `
  -ContentType "application/json" `
  -Body '{"prompt":"create regions for a pop song"}'
```

5. (Optional) Quick smoke test before using REAPER:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

If Ollama is unavailable or returns unusable JSON, the companion falls back to simple built-in rules for:
- `play`
- `stop`
- `tempo <bpm>`
- `select track <n>`
- `mute/unmute track <n>`
- `solo/unsolo track <n>`
- `record arm/disarm track <n>`
- `create regions for a pop song`
- `create regions for a rock song`

Troubleshooting (Ollama):
- If requests fail, confirm the Ollama app/service is running.
- Confirm the model name matches exactly: `qwen2.5:7b-instruct`
- If `.\scripts\run_dev.ps1` fails on a fresh clone, run `uv sync --dev` first.

## Test the ReaImGui Panel (REAPER + API)

1. Install Python dependencies:

```powershell
uv sync --dev
```

2. Start the companion API in file bridge mode:

```powershell
$env:REAPERGPT_REAPER_BRIDGE_TRANSPORT = "file"
.\scripts\run_dev.ps1
```

3. In REAPER, load and run `reaper_bridge/reapergpt_panel.lua` from the Action List.

4. Confirm the ReaperGPT panel opens.

5. Test local panel commands (typed directly into the REAPER panel):
   - `play`
   - `stop`
   - `tempo 128`
   - `select track 1`
   - `solo track 2`
   - `mute track 3`
   - `record arm track 1`

6. Test Python -> REAPER bridge path (while the panel is running):

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/actions `
  -ContentType "application/json" `
  -Body '{"batch":{"actions":[{"type":"transport.play","params":{}}]}}'
```

7. Confirm API health is in file mode:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Expected:
- REAPER transport reacts to `play` / `stop`
- Panel shows local command responses and bridge activity
- `/health` returns `bridge.mode` as `file`

Troubleshooting:
- Install `ReaImGui` via ReaPack if the panel does not open
- Do not run `reapergpt_bridge.lua` and `reapergpt_panel.lua` at the same time
- Keep `reapergpt_panel.lua` running while sending API actions
