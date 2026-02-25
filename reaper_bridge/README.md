# ReaperGPT REAPER Bridge (MVP File Bridge)

This folder contains the REAPER-side Lua scripts for the MVP.

Current status:

- `reapergpt_bridge.lua`:
  - headless file bridge (no UI)
  - polls a command file and executes transport actions
- `reapergpt_panel.lua`:
  - `ReaImGui` chat-style panel inside REAPER
  - also includes file-bridge polling
  - local prompt commands: `play`, `stop`, `tempo <bpm>`

Planned later:

- HTTP/OSC transport
- More action handlers (tempo, markers, track edits)
- Expanded safety checks on the REAPER side

## Which script should I run?

- Use `reapergpt_panel.lua` if you want the in-REAPER chat window (recommended).
- Use `reapergpt_bridge.lua` only if you want a headless bridge without UI.

Do not run both at the same time, because both poll the same bridge files.
