-- ReaperGPT Panel (ReaImGui 0.10.x) - MINIMAL STABLE
-- Goal: prove Begin/End stack is clean and the panel works.

local SCRIPT_NAME = "ReaperGPT"
local LOCK_SECTION = "ReaperGPT"
local LOCK_KEY = "PanelRunning"

-- Single instance lock
if reaper.GetExtState(LOCK_SECTION, LOCK_KEY) == "1" then
  reaper.MB(
    "Panel already running (or lock stuck).\n\nRun: Actions -> Close all running scripts\nRestart REAPER\nThen run again.",
    "ReaperGPT",
    0
  )
  return
end
reaper.SetExtState(LOCK_SECTION, LOCK_KEY, "1", false)

local ctx = reaper.ImGui_CreateContext(SCRIPT_NAME)
local ui_open = true
local first = true
local input_text = ""
local lines = {}

local function cleanup()
  reaper.SetExtState(LOCK_SECTION, LOCK_KEY, "0", false)
  if reaper.ImGui_DestroyContext then
    reaper.ImGui_DestroyContext(ctx)
  end
end

reaper.atexit(cleanup)

local function add_line(s)
  lines[#lines + 1] = tostring(s)
  if #lines > 100 then table.remove(lines, 1) end
end

local function trim(s)
  return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

local function get_track_by_index(track_index)
  if type(track_index) ~= "number" or track_index < 1 then
    return nil, "track index must be >= 1"
  end
  local total = reaper.CountTracks and reaper.CountTracks(0) or 0
  if track_index > total then
    return nil, string.format("track %d does not exist (project has %d)", track_index, total)
  end
  local track = reaper.GetTrack and reaper.GetTrack(0, track_index - 1) or nil
  if not track then
    return nil, string.format("could not resolve track %d", track_index)
  end
  return track
end

local function set_track_flag(track_index, enabled, key, label)
  local track, err = get_track_by_index(track_index)
  if not track then
    add_line("[ERR] " .. err)
    return
  end
  if not reaper.SetMediaTrackInfo_Value then
    add_line("[ERR] REAPER API unavailable: SetMediaTrackInfo_Value")
    return
  end
  reaper.SetMediaTrackInfo_Value(track, key, enabled and 1 or 0)
  if reaper.TrackList_AdjustWindows then
    reaper.TrackList_AdjustWindows(false)
  end
  if reaper.UpdateArrange then
    reaper.UpdateArrange()
  end
  add_line(string.format("%s %s on track %d", label, enabled and "enabled" or "disabled", track_index))
end

local function select_track(track_index)
  local track, err = get_track_by_index(track_index)
  if not track then
    add_line("[ERR] " .. err)
    return
  end
  if reaper.SetOnlyTrackSelected then
    reaper.SetOnlyTrackSelected(track)
  elseif reaper.SetTrackSelected then
    reaper.SetTrackSelected(track, true)
  else
    add_line("[ERR] REAPER API unavailable: track selection")
    return
  end
  add_line(string.format("selected track %d", track_index))
end

local function create_song_form_regions()
  if not reaper.AddProjectMarker2 or not reaper.TimeMap2_QNToTime then
    add_line("[ERR] REAPER region APIs unavailable")
    return
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

  if reaper.Undo_BeginBlock then reaper.Undo_BeginBlock() end
  if reaper.PreventUIRefresh then reaper.PreventUIRefresh(1) end

  local qn = 0
  for i = 1, #sections do
    local section = sections[i]
    local start_time = reaper.TimeMap2_QNToTime(0, qn)
    qn = qn + (section.bars * 4)
    local end_time = reaper.TimeMap2_QNToTime(0, qn)
    reaper.AddProjectMarker2(0, true, start_time, end_time, section.name, -1, 0)
  end

  if reaper.PreventUIRefresh then reaper.PreventUIRefresh(-1) end
  if reaper.UpdateArrange then reaper.UpdateArrange() end
  if reaper.Undo_EndBlock then reaper.Undo_EndBlock("ReaperGPT: create pop song regions", -1) end

  add_line(string.format("created %d regions (default pop song form)", #sections))
end

local function handle_local_command(raw)
  local cmd = trim(raw):lower()
  if cmd == "" then
    return
  end

  if cmd == "play" then
    reaper.OnPlayButton()
    add_line("transport play triggered")
    return
  end

  if cmd == "stop" then
    reaper.OnStopButton()
    add_line("transport stop triggered")
    return
  end

  local bpm = cmd:match("^tempo%s+([%d%.]+)$")
  if bpm then
    local n = tonumber(bpm)
    if n and n > 0 and reaper.SetCurrentBPM then
      reaper.SetCurrentBPM(0, n, true)
      add_line(string.format("tempo set to %.2f BPM", n))
    else
      add_line("[ERR] invalid tempo")
    end
    return
  end

  local track_n = cmd:match("^select%s+track%s+(%d+)$")
  if track_n then
    select_track(tonumber(track_n))
    return
  end

  track_n = cmd:match("^mute%s+track%s+(%d+)$")
  if track_n then
    set_track_flag(tonumber(track_n), true, "B_MUTE", "mute")
    return
  end

  track_n = cmd:match("^unmute%s+track%s+(%d+)$")
  if track_n then
    set_track_flag(tonumber(track_n), false, "B_MUTE", "mute")
    return
  end

  track_n = cmd:match("^solo%s+track%s+(%d+)$")
  if track_n then
    set_track_flag(tonumber(track_n), true, "I_SOLO", "solo")
    return
  end

  track_n = cmd:match("^unsolo%s+track%s+(%d+)$")
  if track_n then
    set_track_flag(tonumber(track_n), false, "I_SOLO", "solo")
    return
  end

  track_n = cmd:match("^record%s+arm%s+track%s+(%d+)$")
  if track_n then
    set_track_flag(tonumber(track_n), true, "I_RECARM", "record arm")
    return
  end

  track_n = cmd:match("^disarm%s+track%s+(%d+)$")
  if track_n then
    set_track_flag(tonumber(track_n), false, "I_RECARM", "record arm")
    return
  end

  if cmd:match("^create%s+regions%s+for%s+a%s+.+song$") or cmd == "create regions for a pop song" then
    create_song_form_regions()
    return
  end

  add_line("[ERR] unknown command: " .. raw)
end

local function loop()
  if first then
    reaper.ImGui_SetNextWindowSize(ctx, 520, 260, reaper.ImGui_Cond_FirstUseEver())
    first = false
  end

  local began = false
  local visible = false

  -- Begin: exact signature for 0.10.x
  local ok, v, open = pcall(reaper.ImGui_Begin, ctx, "ReaperGPT (Minimal)", ui_open)
  if ok then
    began = true
    visible = v
    ui_open = open
  else
    -- If Begin fails, DO NOT End.
    reaper.ShowConsoleMsg("[ReaperGPT] ImGui_Begin failed: " .. tostring(v) .. "\n")
    ui_open = false
  end

  if began and visible then
    reaper.ImGui_TextWrapped(ctx, "Type commands like: play, stop, tempo 128, select track 1")
    reaper.ImGui_Separator(ctx)

    for i = 1, #lines do
      reaper.ImGui_TextWrapped(ctx, lines[i])
    end

    reaper.ImGui_Separator(ctx)

    local submitted
    submitted, input_text = reaper.ImGui_InputText(ctx, "Prompt", input_text,
      reaper.ImGui_InputTextFlags_EnterReturnsTrue())

    reaper.ImGui_SameLine(ctx)
    local clicked = reaper.ImGui_Button(ctx, "Send")

    if submitted or clicked then
      local t = input_text
      input_text = ""
      if t ~= "" then
        add_line("> " .. t)
        handle_local_command(t)
      end
    end
  end

  if began then
    reaper.ImGui_End(ctx)
  end

  if ui_open then
    reaper.defer(loop)
  end
end

loop()
