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
--   track.create / track.set_name / track.set_color / track.set_volume / track.set_pan
--   track.set_input / track.set_stereo / automation.pan_ramp / fx.add / reaper.action

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

  local db = text:match('"db"%s*:%s*(-?[%d%.]+)')
  if db then
    params.db = tonumber(db)
  end

  local start_time_seconds = text:match('"start_time_seconds"%s*:%s*(-?[%d%.]+)')
  if start_time_seconds then
    params.start_time_seconds = tonumber(start_time_seconds)
  end

  local end_time_seconds = text:match('"end_time_seconds"%s*:%s*(-?[%d%.]+)')
  if end_time_seconds then
    params.end_time_seconds = tonumber(end_time_seconds)
  end

  local start_pan = text:match('"start_pan"%s*:%s*(-?[%d%.]+)')
  if start_pan then
    params.start_pan = tonumber(start_pan)
  end

  local end_pan = text:match('"end_pan"%s*:%s*(-?[%d%.]+)')
  if end_pan then
    params.end_pan = tonumber(end_pan)
  end

  local start_db = text:match('"start_db"%s*:%s*(-?[%d%.]+)')
  if start_db then
    params.start_db = tonumber(start_db)
  end

  local end_db = text:match('"end_db"%s*:%s*(-?[%d%.]+)')
  if end_db then
    params.end_db = tonumber(end_db)
  end

  local pan = text:match('"pan"%s*:%s*(-?[%d%.]+)')
  if pan then
    params.pan = tonumber(pan)
  end

  local track_index = text:match('"track_index"%s*:%s*(%d+)')
  if track_index then
    params.track_index = tonumber(track_index)
  end

  local enabled = text:match('"enabled"%s*:%s*(true|false)')
  if enabled ~= nil then
    params.enabled = (enabled == "true")
  end

  local stereo = text:match('"stereo"%s*:%s*(true|false)')
  if stereo ~= nil then
    params.stereo = (stereo == "true")
  end

  local track_ref = text:match('"track_ref"%s*:%s*"([^"]+)"')
  if track_ref then
    params.track_ref = track_ref
  end

  local name = text:match('"name"%s*:%s*"([^"]*)"')
  if name then
    params.name = name
  end

  local color = text:match('"color"%s*:%s*"([^"]+)"')
  if color then
    params.color = color
  end

  local fx_name = text:match('"fx_name"%s*:%s*"([^"]+)"')
  if fx_name then
    params.fx_name = fx_name
  end

  local command_id = text:match('"command_id"%s*:%s*(%d+)')
  if command_id then
    params.command_id = tonumber(command_id)
  end

  local section_id = text:match('"section_id"%s*:%s*(%d+)')
  if section_id then
    params.section_id = tonumber(section_id)
  end

  local command_name = text:match('"command_name"%s*:%s*"([^"]+)"')
  if command_name then
    params.command_name = command_name
  end

  local mode = text:match('"mode"%s*:%s*"([^"]+)"')
  if mode then
    params.mode = mode
  end

  local input_type = text:match('"input_type"%s*:%s*"([^"]+)"')
  if input_type then
    params.input_type = input_type
  end

  local input_index = text:match('"input_index"%s*:%s*(%d+)')
  if input_index then
    params.input_index = tonumber(input_index)
  end

  local midi_channel = text:match('"midi_channel"%s*:%s*(%d+)')
  if midi_channel then
    params.midi_channel = tonumber(midi_channel)
  end

  return params
end

local last_created_track_index = nil

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

local function resolve_track_target(params)
  params = params or {}
  if type(params.track_index) == "number" then
    return get_track_by_index(params.track_index)
  end
  if params.track_ref == "last_created" then
    if not last_created_track_index then
      return nil, "No last_created track available in this REAPER session"
    end
    return get_track_by_index(last_created_track_index)
  end
  return nil, "Missing track target (track_index or track_ref=last_created)"
end

local function color_name_to_native(color_name)
  if type(color_name) ~= "string" or color_name == "" then
    return nil
  end
  local key = trim(color_name):lower()
  local colors = {
    red = {255, 64, 64},
    orange = {255, 153, 51},
    yellow = {255, 220, 64},
    green = {80, 220, 120},
    blue = {80, 140, 255},
    purple = {170, 100, 255},
    pink = {255, 120, 180},
    white = {235, 235, 235},
    black = {32, 32, 32},
  }
  local rgb = colors[key]
  if not rgb or not reaper.ColorToNative then
    return nil
  end
  -- Some REAPER Lua environments choke on bitwise operators; addition is sufficient here.
  return reaper.ColorToNative(rgb[1], rgb[2], rgb[3]) + 0x1000000
end

local function create_track(params)
  params = params or {}
  if not reaper.InsertTrackAtIndex or not reaper.CountTracks or not reaper.GetTrack then
    return "rejected", "REAPER track creation APIs unavailable"
  end

  local index0 = reaper.CountTracks(0)
  if reaper.Undo_BeginBlock then reaper.Undo_BeginBlock() end
  reaper.InsertTrackAtIndex(index0, true)
  local track = reaper.GetTrack(0, index0)
  if not track then
    if reaper.Undo_EndBlock then reaper.Undo_EndBlock("ReaperGPT: create track", -1) end
    return "rejected", "Failed to create track"
  end

  if type(params.name) == "string" and trim(params.name) ~= "" and reaper.GetSetMediaTrackInfo_String then
    reaper.GetSetMediaTrackInfo_String(track, "P_NAME", params.name, true)
  end

  last_created_track_index = index0 + 1

  if reaper.TrackList_AdjustWindows then reaper.TrackList_AdjustWindows(false) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  if reaper.Undo_EndBlock then reaper.Undo_EndBlock("ReaperGPT: create track", -1) end

  local detail = string.format("Created track %d", last_created_track_index)
  if type(params.name) == "string" and trim(params.name) ~= "" then
    detail = detail .. string.format(' ("%s")', params.name)
  end
  return "accepted", detail
end

local function set_track_color(params)
  params = params or {}
  local track, err = resolve_track_target(params)
  if not track then
    return "rejected", err
  end
  if not reaper.SetTrackColor then
    return "rejected", "REAPER SetTrackColor unavailable"
  end
  local native = color_name_to_native(params.color)
  if not native then
    return "rejected", "Unsupported color name (try: red, orange, yellow, green, blue, purple, pink, white, black)"
  end
  reaper.SetTrackColor(track, native)
  if reaper.TrackList_AdjustWindows then reaper.TrackList_AdjustWindows(false) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  return "accepted", string.format("Track color set to %s", tostring(params.color))
end

local function set_track_name(track_index, name)
  local track, err = get_track_by_index(track_index)
  if not track then
    return "rejected", err
  end
  local track_name = trim(name)
  if track_name == "" then
    return "rejected", "name must be a non-empty string"
  end
  if not reaper.GetSetMediaTrackInfo_String then
    return "rejected", "REAPER GetSetMediaTrackInfo_String unavailable"
  end
  reaper.GetSetMediaTrackInfo_String(track, "P_NAME", track_name, true)
  if reaper.TrackList_AdjustWindows then reaper.TrackList_AdjustWindows(false) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  return "accepted", string.format('Track %d renamed to "%s"', track_index, track_name)
end

local function add_fx_to_track(params)
  params = params or {}
  local track, err = resolve_track_target(params)
  if not track then
    return "rejected", err
  end
  local fx_name = trim(params.fx_name)
  if fx_name == "" then
    return "rejected", "fx_name must be a non-empty string"
  end
  if not reaper.TrackFX_AddByName then
    return "rejected", "REAPER TrackFX_AddByName unavailable"
  end
  local fx_index = reaper.TrackFX_AddByName(track, fx_name, false, -1)
  if type(fx_index) ~= "number" or fx_index < 0 then
    return "rejected", 'FX not found or could not be added: "' .. fx_name .. '"'
  end
  return "accepted", string.format('Added FX "%s" (slot %d)', fx_name, fx_index + 1)
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

local function set_track_volume_db(track_index, db)
  local track, err = get_track_by_index(track_index)
  if not track then
    return "rejected", err
  end
  if type(db) ~= "number" then
    return "rejected", "db must be a number"
  end
  if not reaper.SetMediaTrackInfo_Value then
    return "rejected", "REAPER SetMediaTrackInfo_Value unavailable"
  end
  local linear = 10 ^ (db / 20.0)
  reaper.SetMediaTrackInfo_Value(track, "D_VOL", linear)
  if reaper.TrackList_AdjustWindows then
    reaper.TrackList_AdjustWindows(false)
  end
  if reaper.UpdateArrange then
    reaper.UpdateArrange()
  end
  return "accepted", string.format("Track %d volume set to %.2f dB", track_index, db)
end

local function set_track_monitoring(track_index, enabled)
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
  reaper.SetMediaTrackInfo_Value(track, "I_RECMON", enabled and 1 or 0)
  if reaper.TrackList_AdjustWindows then reaper.TrackList_AdjustWindows(false) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  return "accepted", string.format("Track %d monitoring %s", track_index, enabled and "enabled" or "disabled")
end

local function set_track_record_mode(track_index, mode)
  local track, err = get_track_by_index(track_index)
  if not track then
    return "rejected", err
  end
  local mode_key = trim(mode):lower()
  -- Common REAPER record mode values used in many builds.
  local mode_map = {
    input = 0,
    midi_overdub = 7,
    midi_replace = 8,
  }
  local mode_code = mode_map[mode_key]
  if mode_code == nil then
    return "rejected", "Unsupported record mode (use input, midi_overdub, midi_replace)"
  end
  if not reaper.SetMediaTrackInfo_Value then
    return "rejected", "REAPER SetMediaTrackInfo_Value unavailable"
  end
  reaper.SetMediaTrackInfo_Value(track, "I_RECMODE", mode_code)
  if reaper.TrackList_AdjustWindows then reaper.TrackList_AdjustWindows(false) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  return "accepted", string.format("Track %d record mode set to %s", track_index, mode_key)
end

local function set_track_pan(track_index, pan)
  local track, err = get_track_by_index(track_index)
  if not track then
    return "rejected", err
  end
  if type(pan) ~= "number" then
    return "rejected", "pan must be a number"
  end
  if pan < -1 or pan > 1 then
    return "rejected", "pan must be between -1 and 1"
  end
  if not reaper.SetMediaTrackInfo_Value then
    return "rejected", "REAPER SetMediaTrackInfo_Value unavailable"
  end
  reaper.SetMediaTrackInfo_Value(track, "D_PAN", pan)
  if reaper.TrackList_AdjustWindows then reaper.TrackList_AdjustWindows(false) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  return "accepted", string.format("Track %d pan set to %.2f", track_index, pan)
end

local function set_track_input(params)
  params = params or {}
  local track, err = get_track_by_index(params.track_index)
  if not track then
    return "rejected", err
  end
  if not reaper.SetMediaTrackInfo_Value then
    return "rejected", "REAPER SetMediaTrackInfo_Value unavailable"
  end

  local input_type = trim(params.input_type):lower()
  local input_index = tonumber(params.input_index)
  if type(input_index) ~= "number" or input_index < 1 then
    return "rejected", "input_index must be >= 1"
  end

  local rec_input = nil
  if input_type == "audio" then
    local stereo = (params.stereo == true)
    if stereo then
      local stereo_pair_index0 = input_index - 1
      rec_input = 1024 + (stereo_pair_index0 * 2)
    else
      rec_input = input_index - 1
    end
  elseif input_type == "midi" then
    local device_index0 = input_index - 1
    local midi_channel = tonumber(params.midi_channel) or 0 -- 0 = all channels
    if midi_channel < 0 or midi_channel > 16 then
      return "rejected", "midi_channel must be between 0 and 16"
    end
    rec_input = 4096 + (device_index0 * 32) + midi_channel
  else
    return "rejected", "input_type must be audio or midi"
  end

  reaper.SetMediaTrackInfo_Value(track, "I_RECINPUT", rec_input)
  if reaper.TrackList_AdjustWindows then reaper.TrackList_AdjustWindows(false) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end

  if input_type == "midi" then
    return "accepted", string.format(
      "Track %d record input set to MIDI input #%d (channel %s)",
      params.track_index,
      input_index,
      (tonumber(params.midi_channel) or 0) == 0 and "all" or tostring(params.midi_channel)
    )
  end

  return "accepted", string.format(
    "Track %d record input set to %s input #%d",
    params.track_index,
    params.stereo and "stereo" or "audio",
    input_index
  )
end

local function set_track_stereo(track_index, enabled)
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
  -- REAPER tracks are typically even-channel; use 2 for stereo, 1 for minimal/mono-style request.
  reaper.SetMediaTrackInfo_Value(track, "I_NCHAN", enabled and 2 or 1)
  if reaper.TrackList_AdjustWindows then reaper.TrackList_AdjustWindows(false) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  return "accepted", string.format("Track %d set to %s", track_index, enabled and "stereo" or "mono")
end

local function automation_pan_ramp(params)
  params = params or {}
  local track, err = get_track_by_index(params.track_index)
  if not track then
    return "rejected", err
  end
  local start_t = tonumber(params.start_time_seconds)
  local end_t = tonumber(params.end_time_seconds)
  local start_pan = tonumber(params.start_pan)
  local end_pan = tonumber(params.end_pan)
  if not start_t or not end_t or end_t <= start_t then
    return "rejected", "Invalid pan automation time range"
  end
  if not start_pan or start_pan < -1 or start_pan > 1 then
    return "rejected", "start_pan must be between -1 and 1"
  end
  if not end_pan or end_pan < -1 or end_pan > 1 then
    return "rejected", "end_pan must be between -1 and 1"
  end
  if not reaper.GetTrackEnvelopeByName then
    return "rejected", "REAPER GetTrackEnvelopeByName unavailable"
  end
  local env = reaper.GetTrackEnvelopeByName(track, "Pan")
  if not env then
    return "rejected", "Pan envelope not found. Show/enable the Pan envelope, then retry."
  end
  if not reaper.InsertEnvelopePoint or not reaper.Envelope_SortPoints then
    return "rejected", "REAPER envelope point APIs unavailable"
  end

  if reaper.Undo_BeginBlock then reaper.Undo_BeginBlock() end
  reaper.InsertEnvelopePoint(env, start_t, start_pan, 0, 0, false, true)
  reaper.InsertEnvelopePoint(env, end_t, end_pan, 0, 0, false, true)
  reaper.Envelope_SortPoints(env)
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  if reaper.Undo_EndBlock then reaper.Undo_EndBlock("ReaperGPT: pan automation ramp", -1) end

  return "accepted", string.format(
    "Pan automation points added on track %d from %.2fs to %.2fs (%.2f -> %.2f)",
    params.track_index,
    start_t,
    end_t,
    start_pan,
    end_pan
  )
end

local function automation_volume_ramp(params)
  params = params or {}
  local track, err = get_track_by_index(params.track_index)
  if not track then
    return "rejected", err
  end
  local start_t = tonumber(params.start_time_seconds)
  local end_t = tonumber(params.end_time_seconds)
  local start_db = tonumber(params.start_db)
  local end_db = tonumber(params.end_db)
  if not start_t or not end_t or end_t <= start_t then
    return "rejected", "Invalid volume automation time range"
  end
  if start_db == nil or end_db == nil then
    return "rejected", "start_db and end_db are required"
  end
  if not reaper.GetTrackEnvelopeByName then
    return "rejected", "REAPER GetTrackEnvelopeByName unavailable"
  end
  local env = reaper.GetTrackEnvelopeByName(track, "Volume")
  if not env then
    return "rejected", "Volume envelope not found. Show/enable the Volume envelope, then retry."
  end
  if not reaper.InsertEnvelopePoint or not reaper.Envelope_SortPoints then
    return "rejected", "REAPER envelope point APIs unavailable"
  end

  local start_val = 10 ^ (start_db / 20.0)
  local end_val = 10 ^ (end_db / 20.0)
  if reaper.Undo_BeginBlock then reaper.Undo_BeginBlock() end
  reaper.InsertEnvelopePoint(env, start_t, start_val, 0, 0, false, true)
  reaper.InsertEnvelopePoint(env, end_t, end_val, 0, 0, false, true)
  reaper.Envelope_SortPoints(env)
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  if reaper.Undo_EndBlock then reaper.Undo_EndBlock("ReaperGPT: volume automation ramp", -1) end

  return "accepted", string.format(
    "Volume automation points added on track %d from %.2fs to %.2fs (%.2f dB -> %.2f dB)",
    params.track_index,
    start_t,
    end_t,
    start_db,
    end_db
  )
end

local function run_reaper_action(params)
  params = params or {}
  local section_id = tonumber(params.section_id) or 0
  local command_id = params.command_id
  if type(command_id) ~= "number" and type(params.command_name) == "string" then
    local name = trim(params.command_name)
    if name == "" then
      return "rejected", "command_name must be a non-empty string"
    end
    if not reaper.NamedCommandLookup then
      return "rejected", "REAPER NamedCommandLookup unavailable"
    end
    command_id = reaper.NamedCommandLookup(name)
    if type(command_id) ~= "number" or command_id <= 0 then
      return "rejected", 'Could not resolve command_name "' .. name .. '"'
    end
  end
  if type(command_id) ~= "number" or command_id <= 0 then
    return "rejected", "reaper.action requires command_id or command_name"
  end
  if section_id ~= 0 then
    return "rejected", "reaper.action currently supports only main section (section_id=0)"
  end
  if not reaper.Main_OnCommand then
    return "rejected", "REAPER Main_OnCommand unavailable"
  end
  reaper.Main_OnCommand(command_id, 0)
  return "accepted", string.format("Executed REAPER action %d (section %d)", command_id, section_id)
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
  if action_type == "track.create" then
    return create_track(params)
  end
  if action_type == "track.select" then
    return select_track(params.track_index)
  end
  if action_type == "track.set_name" then
    return set_track_name(params.track_index, params.name)
  end
  if action_type == "track.set_color" then
    return set_track_color(params)
  end
  if action_type == "track.set_volume" then
    return set_track_volume_db(params.track_index, params.db)
  end
  if action_type == "track.set_pan" then
    return set_track_pan(params.track_index, params.pan)
  end
  if action_type == "track.set_input" then
    return set_track_input(params)
  end
  if action_type == "track.set_stereo" then
    return set_track_stereo(params.track_index, params.enabled)
  end
  if action_type == "track.set_monitoring" then
    return set_track_monitoring(params.track_index, params.enabled)
  end
  if action_type == "track.set_record_mode" then
    return set_track_record_mode(params.track_index, params.mode)
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
  if action_type == "fx.add" then
    return add_fx_to_track(params)
  end
  if action_type == "automation.pan_ramp" then
    return automation_pan_ramp(params)
  end
  if action_type == "automation.volume_ramp" then
    return automation_volume_ramp(params)
  end
  if action_type == "reaper.action" then
    return run_reaper_action(params)
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
log("Supported actions: transport.play, transport.stop, project.set_tempo, regions.create_song_form, track.create, track.select, track.set_name, track.set_color, track.set_volume, track.set_pan, track.set_input, track.set_stereo, track.set_monitoring, track.set_record_mode, track.mute, track.solo, track.record_arm, fx.add, automation.pan_ramp, automation.volume_ramp, reaper.action")
tick()
