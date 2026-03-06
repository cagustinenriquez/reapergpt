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

## Auto-starting bridge + panel

`reapergpt_startup_launcher.lua` loads both the headless bridge and the panel scripts, and `reapergpt_setup_autostart.lua`
registers that launcher as REAPER’s global startup action (requires SWS 2.13+ to call `NF_SetGlobalStartupAction`).citeturn6search0

To enable it:
1. Install the SWS extension (2.13 or newer).
2. Place the four `reaper_bridge/*.lua` scripts inside your REAPER `Scripts` folder.
3. Run `reapergpt_setup_autostart.lua` from REAPER once; the success dialog will confirm the global startup action was set.
4. Restart REAPER — the bridge and panel will launch automatically.

If you prefer manual control, run `reapergpt_panel.lua` for the UI or `reapergpt_bridge.lua` for the headless bridge.

Do not run `reapergpt_panel.lua` and `reapergpt_bridge.lua` twice at once, because both poll the same bridge files.
