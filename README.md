# Reaper Agent MVP

This project is the Python side of the DAW Agent MVP described in the "DAW Agent Reaper MVP Blueprint". It exposes a FastAPI service that receives natural-language prompts, plans structured REAPER actions, and sends validated step lists to REAPER through a file-based bridge.

## Local development

1. Create or activate a Python 3.10+ virtual environment:
   ```bash
   python -m venv .venv
   .venv\\Scripts\\activate  # Windows
   source .venv/bin/activate # macOS / Linux
   ```
2. Install the dependencies:
   ```bash
   pip install -e .[dev]
   ```
3. Start the FastAPI server:
   ```bash
   uvicorn companion.main:app --reload
   ```

## File bridge

The current transport is file-based because it is the fastest way to get a true round trip working inside REAPER.

- Python writes [data/reaper_bridge/pending_plan.json](/c:/Users/cenriquez/Desktop/reapergpt/data/reaper_bridge/pending_plan.json).
- REAPER runs [reapergpt_file_bridge.lua](/c:/Users/cenriquez/Desktop/reapergpt/reaper_bridge/reapergpt_file_bridge.lua), polls that file, executes supported tools, then writes:
- [execution_result.json](/c:/Users/cenriquez/Desktop/reapergpt/data/reaper_bridge/execution_result.json)
- [project_state.json](/c:/Users/cenriquez/Desktop/reapergpt/data/reaper_bridge/project_state.json)

Supported tools in the current REAPER bridge:

- `create_track`
- `create_bus`
- `create_send`
- `insert_fx`
- `set_track_color`
- `project.set_tempo`

Start REAPER, run [reapergpt_file_bridge.lua](/c:/Users/cenriquez/Desktop/reapergpt/reaper_bridge/reapergpt_file_bridge.lua) from the Action List, and keep it running while the API is up.

## API endpoints

- `GET /health`: Returns planner configuration so the UI knows whether heuristic fallback is enabled.
- `GET /state/project`: Returns the latest snapshot from `project_state.json`.
- `POST /plan`: Accepts `{ "prompt": "...", "state": { ... } }` and returns a `PlanResponse` with a summary, source, and steps. Currently implemented heuristics cover:
  - Drum bus creation (detects Kick/Snare/Drum names in the provided `state`).
  - Vocal session template (creates Lead Vocal, double tracks, buses, FX, and routing).
  - Transport control (`play` / `stop`).
  - Tempo adjustments (`tempo 128`).
- `POST /execute-plan`: Writes the validated plan to `pending_plan.json`, waits for `execution_result.json`, and then returns the execution results plus the refreshed project state.

Example request:

```json
POST /plan
{
  "prompt": "Create a vocal session with Lead Vocal, Double L, Double R, Vocal Bus, Reverb Bus."
}
```

Example response:

```json
{
  "ok": true,
  "summary": "Create a vocal session template with buses, FX, and routing.",
  "source": "heuristic",
  "steps": [
    { "tool": "create_track", "args": { "name": "Lead Vocal" } },
    ...
    { "tool": "create_send", "args": { "src": { "type": "track_name", "value": "Vocal Bus" }, "dst": { "type": "track_name", "value": "Reverb Bus" } } }
  ]
}
```

Once `/plan` returns a step list, POST the same payload to `/execute-plan` to exercise the bridge and get the final state snapshot for rendering inside the REAPER UI.

Example:

```powershell
Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/execute-plan `
  -ContentType "application/json" `
  -Body '{
    "steps": [
      { "tool": "create_track", "args": { "name": "Lead Vocal" } },
      { "tool": "create_bus", "args": { "name": "Vocal Bus" } },
      {
        "tool": "create_send",
        "args": {
          "src": { "type": "track_name", "value": "Lead Vocal" },
          "dst": { "type": "track_name", "value": "Vocal Bus" }
        }
      }
    ]
  }'
```

## Testing

Run the API test suite with:

```bash
pytest -q
```

The tests cover the `/health`, `/state/project`, and `/plan` endpoints with the current heuristic planner.
