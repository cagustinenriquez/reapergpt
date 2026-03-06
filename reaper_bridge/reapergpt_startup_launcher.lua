--[[
Brings up the ReaperGPT bridge + panel automatically.

The SWS global startup action (or __startup.lua) can point at this script so that
both the bridge and the ReaImGui panel launch whenever REAPER starts.
]]

local function normalize_path(path)
  if not path then return path end
  return path:gsub("\\", "/")
end

local function file_exists(path)
  local f = io.open(path, "rb")
  if not f then
    return false
  end
  f:close()
  return true
end

local function find_script(script_name)
  local candidates = {}
  local info = debug.getinfo(1, "S")
  local source = info and info.source
  if type(source) == "string" and source:sub(1, 1) == "@" then
    local my_path = normalize_path(source:sub(2))
    local dir = my_path:match("(.*/)")
    if dir then
      candidates[#candidates + 1] = dir
      candidates[#candidates + 1] = dir .. "scripts/"
      candidates[#candidates + 1] = dir .. "../Scripts/"
    end
  end
  local resource_scripts = normalize_path(reaper.GetResourcePath()) .. "/Scripts/"
  candidates[#candidates + 1] = resource_scripts
  candidates[#candidates + 1] = normalize_path(reaper.GetResourcePath()) .. "/reaper_bridge/"

  for _, base in ipairs(candidates) do
    local path = base .. script_name
    if file_exists(path) then
      return path
    end
  end
  return nil
end

local function launch_script(path)
  if not path then
    return false, "no path"
  end
  local chunk, load_err = loadfile(path)
  if not chunk then
    return false, load_err
  end
  local ok, run_err = pcall(chunk)
  if not ok then
    return false, run_err
  end
  return true
end

local function note(message)
  reaper.ShowConsoleMsg("[ReaperGPT] " .. message .. "\n")
end

local targets = {
  "reapergpt_bridge.lua",
  "reapergpt_panel.lua",
}

local failures = {}
for _, script in ipairs(targets) do
  local path = find_script(script)
  if not path then
    failures[#failures + 1] = string.format("%s not found", script)
  else
    local ok, err = launch_script(path)
    if not ok then
      failures[#failures + 1] = string.format("%s failed: %s", script, tostring(err))
    end
  end
end

if #failures > 0 then
  note("Auto-start failed (" .. table.concat(failures, "; ") .. ")")
else
  note("Bridge + panel auto-started")
end
