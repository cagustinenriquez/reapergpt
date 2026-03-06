# TODO

## Autonomous DAW Reasoning Roadmap

### Phase 1: Project State (Make The Assistant "See")
- Add REAPER bridge state snapshot support (tracks, sends/receives, FX chains, selected track/item/region, markers/regions, envelopes summary).
- Add backend endpoint: `GET /state/project`.
- Return a structured project snapshot the planner can consume.
- Add tests for state response shape and basic parsing.

### Phase 2: Planning Without Immediate Execution
- Add `POST /plan` endpoint (goal -> proposed actions, no dispatch).
- Return:
  - proposed actions
  - rationale
  - assumptions
  - risks
  - clarifying questions (if needed)
- Keep `/prompt` for quick one-shot commands.

### Phase 3: Multi-Step Execution With Checkpoints
- Add `POST /execute-plan` endpoint for approved plans.
- Execute plans in stages (template/routing/FX/levels/automation).
- Stop on failure and return partial progress + failure detail.
- Add per-step result reporting in the panel/backend logs.

### Phase 4: Memory / Preferences
- Add persistent user preferences/profile storage:
  - preferred plugins
  - genre/style defaults
  - track naming/color conventions
  - routing template defaults
- Use profile data during planning (e.g., "punk", "clean indie", etc.).

### Phase 5: Verification / Repair Loop
- Re-read REAPER state after execution.
- Confirm expected outcomes (FX inserted, sends created, envelopes added, etc.).
- If mismatch:
  - retry safely, or
  - return clarification/error with suggested fixes.
- Have `/execute-plan` return the final validated project snapshot plus any bridge fetch error so the panel can triage success/failure loops directly.

## UX / Panel Improvements
- Add panel "assistant mode" flow:
  - prompt -> clarification (if needed) -> plan preview -> execute
- Show backend planner source in panel (`kimi` / `opencode` / `heuristic`)
- Show bridge status + last action result summary in panel
- Improve AI response display to show per-action accepted/rejected details

## Template / Style Intelligence
- Use clarification answers (`fx_setup`, `sound_style`) to build actual FX chains and buses.
- Add genre/style starter templates:
  - punk
  - clean indie
  - classic rock
  - metal
  - lo-fi
- Add optional bus creation for templates (drum bus, guitar bus, vocal bus).

## Action Catalog Expansion (Structured)
- Track:
  - `track.set_send_volume`
  - `track.set_send_pre_fader`
  - `track.remove_send`
  - more `track.set_record_mode` values
  - explicit MIDI input channel parsing (e.g. `midi #1 ch 2`)
- Item:
  - `item.select`
  - `item.move`
  - `item.split`
  - `item.set_gain`
  - `item.set_fade`
- Project:
  - `project.time_selection`
  - `project.loop_points`
  - more render formats/options (`wav`, `flac`, filename pattern)
- Automation:
  - richer pan/volume value phrase parsing
  - more envelope lanes (mute/send/fx params)

## Reliability / Dev Workflow
- Persist clarification sessions (not in-memory only).
- Add `/health` fields for active LLM provider + planner status.
- Add integration tests for:
  - clarification flow
  - file bridge roundtrip (mocked)
  - plan/execute workflows (future)
