# Session Builder Live Validation

This checklist is for validating the typed Session Builder execution path in a real REAPER session.

## Preconditions

- Start the FastAPI server.
- Launch `reaper_bridge/reapergpt_file_bridge.lua` inside REAPER.
- Launch `reaper_bridge/reapergpt_panel.lua` inside REAPER.
- Use a new empty REAPER project unless a scenario says otherwise.
- Confirm the panel shows a connected project state before testing apply.

## Scenario 1: Create One Track

Prompt:

`create Lead Vocal`

Validate:

- Preview shows one `track.create` action.
- Apply succeeds.
- A new track named `Lead Vocal` exists in REAPER.
- Apply results include `track_id` or `track_index`.
- Verification reports `track_created`.

## Scenario 2: Create Bus Plus Two Tracks

Prompt:

`create Lead Vocal, Double L, Double R, and Vocal Bus`

Validate:

- Preview order matches the intended insertion order.
- Apply creates four tracks in that order.
- `Vocal Bus` exists with the expected name.
- Verification reports one `bus_created` and the expected `track_created` checks.

## Scenario 3: Route Tracks To Bus

Prompt:

`create Lead Vocal, Double L, Double R, Vocal Bus, and route all vocals to the bus`

Validate:

- Preview contains `track.create`, `bus.create`, and `send.create`.
- Apply succeeds.
- Each vocal track has a send to `Vocal Bus`.
- Send mode matches the previewed mode.
- `send.create` result includes resolved source and destination track ids/names.
- Verification reports `send_exists` for each requested route.

## Scenario 4: Invalid Target Ref

Prepare:

- Use a plan whose `send.create` references a missing or invalid target.

Validate:

- Apply fails on the expected action.
- Error text includes the failed `action_id`.
- Error text includes the action name and the reason.
- Result detail includes the action input and `completed_action_ids`.
- Earlier successful actions remain visible in the result list.

## Scenario 5: Repeat Apply / Duplicate Names

Prompt:

`create Lead Vocal and Vocal Bus`

Validate:

- Apply once and confirm success.
- Apply the same preview again.
- Confirm whether duplicate names are created or rejected.
- Record the current behavior as the baseline until deduplication rules are implemented.
- Verification still matches the behavior actually produced by REAPER.

## What To Capture

- Screenshot of preview before apply.
- Screenshot of REAPER track list after apply.
- `data/reaper_bridge/execution_result.json`
- `data/reaper_bridge/project_state.json`
- `data/reaper_bridge_debug.log`

## Pass Criteria

- Previewed typed actions match what REAPER executed.
- Action ids in results line up with the plan preview.
- Final project state confirms track creation, bus creation, and send routing.
- Failures clearly identify the failing action and preserve prior successes.
