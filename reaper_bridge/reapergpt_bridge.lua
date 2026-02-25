-- ReaperGPT REAPER bridge (MVP file-bridge implementation)
-- Run this script in REAPER and leave it running. It polls a command file in:
--   <REAPER resource path>/ReaperGPTBridge/commands.txt
-- and writes responses to:
--   <REAPER resource path>/ReaperGPTBridge/responses.txt
--
-- Supported real actions in this MVP:
--   transport.play
--   transport.stop
--   regions.create_song_form (default pop template)

local BRIDGE_POLL_MS = 100
local BRIDGE_DIR = reaper.GetResourcePath() .. "/ReaperGPTBridge"
local COMMAND_FILE = BRIDGE_DIR .. "/commands.txt"
local RESPONSE_FILE = BRIDGE_DIR .. "/responses.txt"

local function log(msg)
  reaper.ShowConsoleMsg("[ReaperGPT Bridge] " .. tostring(msg) .. "\n")
end

local function ensure_bridge_dir()
  reaper.RecursiveCreateDirectory(BRIDGE_DIR, 0)
end

local function read_file(path)
  local f = io.open(path, "r")
  if not f then
    return nil
  end
  local content = f:read("*a")
  f:close()
  return content
end

local function write_file(path, content)
  local f = io.open(path, "w")
  if not f then
    return false
  end
  f:write(content)
  f:close()
  return true
end

local function clear_file(path)
  write_file(path, "")
end

local function split_lines(s)
  local lines = {}
  for line in string.gmatch(s or "", "[^\r\n]+") do
    if line ~= "" then
      table.insert(lines, line)
    end
  end
  return lines
end

local function trim(s)
  return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

local function parse_bridge_params_json(raw)
  local params = {}
  local text = trim(raw)
  if text == "" or text == "{}" then
    return params
  end

  local bpm = text:match('"bpm"%s*:%s*([%d%.]+)')
  if bpm then
    params.bpm = tonumber(bpm)
  end

  local track_index = text:match('"track_index"%s*:%s*(%d+)')
  if track_index then
    params.track_index = tonumber(track_index)
  end

  local enabled = text:match('"enabled"%s*:%s*(true|false)')
  if enabled ~= nil then
    params.enabled = (enabled == "true")
  end

  return params
end

local function get_track_by_index(track_index)
  if type(track_index) ~= "number" or track_index < 1 then
    return nil, "track_index must be >= 1"
  end
  local total = reaper.CountTracks and reaper.CountTracks(0) or 0
  if track_index > total then
    return nil, string.format("Track %d does not exist (project has %d tracks)", track_index, total)
  end
  local track = reaper.GetTrack and reaper.GetTrack(0, track_index - 1) or nil
  if not track then
    return nil, string.format("Could not resolve track %d", track_index)
  end
  return track, nil
end

local function set_project_tempo(bpm)
  if type(bpm) ~= "number" or bpm <= 0 then
    return "rejected", "bpm must be > 0"
  end
  reaper.SetCurrentBPM(0, bpm, true)
  return "accepted", string.format("Project tempo set to %.2f BPM", bpm)
end

local function select_track(track_index)
  local track, err = get_track_by_index(track_index)
  if not track then
    return "rejected", err
  end
  if reaper.SetOnlyTrackSelected then
    reaper.SetOnlyTrackSelected(track)
  elseif reaper.SetTrackSelected then
    reaper.SetTrackSelected(track, true)
  else
    return "rejected", "REAPER track selection API unavailable"
  end
  return "accepted", string.format("Selected track %d", track_index)
end

local function set_track_flag(track_index, enabled, key, label)
  local track, err = get_track_by_index(track_index)
  if not track then
    return "rejected", err
  end
  if type(enabled) ~= "boolean" then
    return "rejected", "enabled must be true or false"
  end
  if not reaper.SetMediaTrackInfo_Value then
    return "rejected", "REAPER SetMediaTrackInfo_Value unavailable"
  end
  reaper.SetMediaTrackInfo_Value(track, key, enabled and 1 or 0)
  if reaper.TrackList_AdjustWindows then
    reaper.TrackList_AdjustWindows(false)
  end
  if reaper.UpdateArrange then
    reaper.UpdateArrange()
  end
  return "accepted", string.format("%s %s on track %d", label, enabled and "enabled" or "disabled", track_index)
end

local function create_song_form_regions()
  if not reaper.AddProjectMarker2 then
    return "rejected", "REAPER API AddProjectMarker2 unavailable"
  end
  if not reaper.TimeMap2_QNToTime then
    return "rejected", "REAPER API TimeMap2_QNToTime unavailable"
  end

  local sections = {
    { name = "Intro", bars = 8 },
    { name = "Verse 1", bars = 16 },
    { name = "Pre-Chorus", bars = 8 },
    { name = "Chorus 1", bars = 16 },
    { name = "Verse 2", bars = 16 },
    { name = "Pre-Chorus 2", bars = 8 },
    { name = "Chorus 2", bars = 16 },
    { name = "Bridge", bars = 8 },
    { name = "Final Chorus", bars = 16 },
    { name = "Outro", bars = 8 },
  }

  if reaper.Undo_BeginBlock then
    reaper.Undo_BeginBlock()
  end
  if reaper.PreventUIRefresh then
    reaper.PreventUIRefresh(1)
  end

  local qn = 0
  for i = 1, #sections do
    local section = sections[i]
    local start_time = reaper.TimeMap2_QNToTime(0, qn)
    qn = qn + (section.bars * 4)
    local end_time = reaper.TimeMap2_QNToTime(0, qn)
    reaper.AddProjectMarker2(0, true, start_time, end_time, section.name, -1, 0)
  end

  if reaper.PreventUIRefresh then
    reaper.PreventUIRefresh(-1)
  end
  if reaper.UpdateArrange then
    reaper.UpdateArrange()
  end
  if reaper.Undo_EndBlock then
    reaper.Undo_EndBlock("ReaperGPT: create pop song regions", -1)
  end

  return "accepted", string.format("Created %d regions (default pop song form)", #sections)
end

local function dispatch_action(action_type, params)
  params = params or {}
  if action_type == "transport.play" then
    reaper.OnPlayButton()
    return "accepted", "REAPER transport play triggered"
  end
  if action_type == "transport.stop" then
    reaper.OnStopButton()
    return "accepted", "REAPER transport stop triggered"
  end
  if action_type == "project.set_tempo" then
    return set_project_tempo(params.bpm)
  end
  if action_type == "track.select" then
    return select_track(params.track_index)
  end
  if action_type == "track.mute" then
    return set_track_flag(params.track_index, params.enabled, "B_MUTE", "Mute")
  end
  if action_type == "track.solo" then
    return set_track_flag(params.track_index, params.enabled, "I_SOLO", "Solo")
  end
  if action_type == "track.record_arm" then
    return set_track_flag(params.track_index, params.enabled, "I_RECARM", "Record arm")
  end
  if action_type == "regions.create_song_form" then
    return create_song_form_regions()
  end
  return "rejected", "Unsupported action in Lua file bridge MVP"
end

local function process_commands(raw)
  local lines = split_lines(raw)
  if #lines == 0 then
    return nil
  end

  local batch_header = lines[1]
  if not string.match(batch_header, "^batch_id=") then
    return nil
  end

  local out = { batch_header }
  for i = 2, #lines do
    local line = lines[i]
    local request_id, action_type, params_raw = string.match(line, "^([^\t]+)\t([^\t]+)\t?(.*)$")
    if request_id and action_type then
      local status, detail = dispatch_action(action_type, parse_bridge_params_json(params_raw))
      table.insert(out, request_id .. "\t" .. status .. "\t" .. detail)
    end
  end

  return table.concat(out, "\n") .. "\n"
end

local last_seen = nil

local function tick()
  ensure_bridge_dir()

  local raw = read_file(COMMAND_FILE)
  if raw and raw ~= "" and raw ~= last_seen then
    last_seen = raw
    local response = process_commands(raw)
    if response then
      write_file(RESPONSE_FILE, response)
      clear_file(COMMAND_FILE)
    end
  end

  reaper.defer(tick)
end

ensure_bridge_dir()
log("File bridge active: " .. BRIDGE_DIR)
log("Supported actions: transport.play, transport.stop, project.set_tempo, regions.create_song_form, track.select, track.mute, track.solo, track.record_arm")
tick()
