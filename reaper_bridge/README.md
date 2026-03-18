# REAPER File Bridge

Run [reapergpt_file_bridge.lua](/c:/Users/cenriquez/Desktop/reapergpt/reaper_bridge/reapergpt_file_bridge.lua) inside REAPER and keep it running.

It watches:

- [pending_plan.json](/c:/Users/cenriquez/Desktop/reapergpt/data/reaper_bridge/pending_plan.json)

It writes:

- [execution_result.json](/c:/Users/cenriquez/Desktop/reapergpt/data/reaper_bridge/execution_result.json)
- [project_state.json](/c:/Users/cenriquez/Desktop/reapergpt/data/reaper_bridge/project_state.json)

Supported tools:

- `create_track`
- `create_bus`
- `create_send`
- `insert_fx`
- `set_track_color`
- `project.set_tempo`
