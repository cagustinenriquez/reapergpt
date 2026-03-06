--[[
Use this script (requires SWS 2.13+) to register ReaperGPT’s startup helper as the global startup action.

Once executed, REAPER will run `reapergpt_startup_launcher.lua` automatically at launch,
which in turn fires up both the bridge and the ReaImGui panel.
]]

local function find_launcher()
  local info = debug.getinfo(1, "S")
  local source = info and info.source
  local script_dir
  if type(source) == "string" and source:sub(1, 1) == "@" then
    script_dir = source:sub(2):match("(.*/)")
  end
  local candidates = {}
  if script_dir then
    candidates[#candidates + 1] = script_dir .. "reapergpt_startup_launcher.lua"
    candidates[#candidates + 1] = script_dir .. "../reaper_bridge/reapergpt_startup_launcher.lua"
    candidates[#candidates + 1] = script_dir .. "scripts/reapergpt_startup_launcher.lua"
  end
  candidates[#candidates + 1] = reaper.GetResourcePath() .. "/Scripts/reapergpt_startup_launcher.lua"
  candidates[#candidates + 1] = reaper.GetResourcePath() .. "/scripts/reapergpt_startup_launcher.lua"
  candidates[#candidates + 1] = reaper.GetResourcePath() .. "/reaper_bridge/reapergpt_startup_launcher.lua"

  for _, path in ipairs(candidates) do
    local f = io.open(path, "rb")
    if f then
      f:close()
      return path
    end
  end
  return nil
end

local SWS_FN = "NF_SetGlobalStartupAction"
if not reaper.APIExists(SWS_FN) then
  reaper.ShowMessageBox(
    "SWS extension 2.13+ is required to set the global startup action.",
    "ReaperGPT Auto-Start",
    0
  )
  return
end

local launcher_path = find_launcher()
if not launcher_path then
  reaper.ShowMessageBox(
    "Could not locate reapergpt_startup_launcher.lua. "
      .. "Place it next to this script or inside your REAPER/Scripts folder.",
    "ReaperGPT Auto-Start",
    0
  )
  return
end

local ext_state_key = "startup_launcher_cmd_id"
local stored_id = tonumber(reaper.GetExtState("ReaperGPT", ext_state_key) or "")
local launcher_cmd_id
if stored_id and stored_id ~= 0 then
  local name = reaper.ReverseNamedCommandLookup(stored_id)
  if name and name ~= "" then
    launcher_cmd_id = stored_id
  end
end

if not launcher_cmd_id then
  launcher_cmd_id = reaper.AddRemoveReaScript(true, 0, launcher_path, true)
  if launcher_cmd_id == 0 then
    reaper.ShowMessageBox(
      "Unable to register the startup helper script as an action.",
      "ReaperGPT Auto-Start",
      0
    )
    return
  end
  reaper.SetExtState("ReaperGPT", ext_state_key, tostring(launcher_cmd_id), true)
end

local command_name = reaper.ReverseNamedCommandLookup(launcher_cmd_id)
if not command_name or command_name == "" then
  reaper.ShowMessageBox(
    "Startup helper action missing command name.",
    "ReaperGPT Auto-Start",
    0
  )
  return
end

local identifier = "_" .. command_name
local get_fn_exists = reaper.APIExists("NF_GetGlobalStartupAction")
local current_action = get_fn_exists and reaper.NF_GetGlobalStartupAction()
local action_already_set = current_action == identifier
if action_already_set then
  reaper.ShowMessageBox(
    "ReaperGPT auto-start is already configured.",
    "ReaperGPT Auto-Start",
    0
  )
  return
end

local ok = reaper.NF_SetGlobalStartupAction(identifier)
if ok then
  reaper.ShowMessageBox(
    "ReaperGPT auto-start has been registered. Restart REAPER to launch the bridge + panel automatically.",
    "ReaperGPT Auto-Start",
    0
  )
else
  reaper.ShowMessageBox(
    "Failed to set the global startup action. Ensure SWS is up to date.",
    "ReaperGPT Auto-Start",
    0
  )
end
