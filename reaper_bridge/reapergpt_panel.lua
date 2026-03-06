-- ReaperGPT Panel (ReaImGui 0.10.x) - MINIMAL STABLE
-- Goal: prove Begin/End stack is clean and the panel works.

local SCRIPT_NAME = "ReaperGPT"
local LOCK_SECTION = "ReaperGPT"
local LOCK_KEY = "PanelRunning"
local COMPANION_PROMPT_URL = "http://127.0.0.1:8000/prompt"
local BRIDGE_DIR = reaper.GetResourcePath() .. "/ReaperGPTBridge"
local PANEL_PROMPT_PAYLOAD_FILE = BRIDGE_DIR .. "/panel_prompt_payload.json"
local PANEL_PROMPT_LOG_FILE = BRIDGE_DIR .. "/panel_prompt_log.txt"

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
local prompt_history = {}
local prompt_history_index = nil
local prompt_history_draft = ""
local ai_responses = {}
local ai_selected_index = nil

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

local function add_history_entry(s)
  local t = trim(s)
  if t == "" then return end
  if prompt_history[#prompt_history] ~= t then
    prompt_history[#prompt_history + 1] = t
    if #prompt_history > 100 then
      table.remove(prompt_history, 1)
    end
  end
  prompt_history_index = nil
  prompt_history_draft = ""
end

local function clip_text(s, n)
  local t = tostring(s or "")
  local limit = tonumber(n) or 80
  if #t <= limit then return t end
  return t:sub(1, limit - 3) .. "..."
end

local function add_ai_response_entry(prompt, raw_response, summary)
  ai_responses[#ai_responses + 1] = {
    prompt = tostring(prompt or ""),
    raw = tostring(raw_response or ""),
    summary = tostring(summary or ""),
    timestamp = (os.date and os.date("%H:%M:%S")) or "",
  }
  if #ai_responses > 50 then
    table.remove(ai_responses, 1)
  end
  ai_selected_index = #ai_responses
end

local function copy_to_clipboard(text)
  local t = tostring(text or "")
  if reaper.ImGui_SetClipboardText then
    local ok = pcall(reaper.ImGui_SetClipboardText, ctx, t)
    if ok then return true end
  end
  if reaper.CF_SetClipboard then
    local ok = pcall(reaper.CF_SetClipboard, t)
    if ok then return true end
  end
  return false
end

local function ensure_bridge_dir()
  if reaper.RecursiveCreateDirectory then
    reaper.RecursiveCreateDirectory(BRIDGE_DIR, 0)
  end
end

local function write_file(path, content)
  local f = io.open(path, "w")
  if not f then return false end
  f:write(content)
  f:close()
  return true
end

local function append_file(path, content)
  local f = io.open(path, "a")
  if not f then return false end
  f:write(content)
  f:close()
  return true
end

local function json_escape(s)
  s = tostring(s or "")
  s = s:gsub("\\", "\\\\")
  s = s:gsub('"', '\\"')
  s = s:gsub("\r", "\\r")
  s = s:gsub("\n", "\\n")
  s = s:gsub("\t", "\\t")
  return s
end

local function shell_quote(s)
  return '"' .. tostring(s or ""):gsub('"', '\\"') .. '"'
end

local function log_prompt_event(kind, prompt, detail)
  ensure_bridge_dir()
  local stamp = os.date and os.date("%Y-%m-%d %H:%M:%S") or "unknown-time"
  local line = string.format(
    "[%s] %s | prompt=%s",
    stamp,
    tostring(kind or "unknown"),
    tostring(prompt or ""):gsub("[\r\n]+", " ")
  )
  if detail and tostring(detail) ~= "" then
    line = line .. " | " .. tostring(detail):gsub("[\r\n]+", " ")
  end
  append_file(PANEL_PROMPT_LOG_FILE, line .. "\n")
end

local function exec_command(command, timeout_ms)
  if reaper.ExecProcess then
    local a, b = reaper.ExecProcess(command, timeout_ms or 10000)
    if type(a) == "number" and type(b) == "string" then
      return a, b
    end
    if type(a) == "string" then
      return 0, a
    end
  end

  local p = io.popen(command .. " 2>&1", "r")
  if not p then
    return 1, "Failed to execute command"
  end
  local out = p:read("*a") or ""
  p:close()
  return 0, out
end

local function is_key_pressed(key_fn)
  if not (reaper.ImGui_IsKeyPressed and key_fn) then
    return false
  end
  local ok_key, key = pcall(key_fn)
  if not ok_key then
    return false
  end
  local ok_pressed, pressed = pcall(reaper.ImGui_IsKeyPressed, ctx, key)
  if not ok_pressed then
    return false
  end
  return pressed == true
end

local function summarize_prompt_response(raw)
  local t = tostring(raw or "")
  local detail = t:match('"detail"%s*:%s*"([^"]+)"')
  if detail and detail ~= "" then
    return detail
  end
  local accepted = 0
  local rejected = 0
  for status in t:gmatch('"status"%s*:%s*"(accepted|rejected)"') do
    if status == "accepted" then accepted = accepted + 1 end
    if status == "rejected" then rejected = rejected + 1 end
  end
  if accepted > 0 or rejected > 0 then
    return string.format("AI dispatch results: %d accepted, %d rejected", accepted, rejected)
  end
  t = trim(t):gsub("%s+", " ")
  if #t > 180 then
    t = t:sub(1, 177) .. "..."
  end
  return t ~= "" and t or "No response body"
end

local function submit_prompt_to_companion(prompt)
  ensure_bridge_dir()
  local payload = '{"prompt":"' .. json_escape(prompt) .. '"}'
  if not write_file(PANEL_PROMPT_PAYLOAD_FILE, payload) then
    add_line("[ERR] could not write prompt payload file")
    log_prompt_event("ai_error", prompt, "could not write payload file")
    return false
  end

  local cmd = table.concat({
    "curl.exe",
    "-sS",
    "--max-time", "8",
    "-H", shell_quote("Content-Type: application/json"),
    "-X", "POST",
    shell_quote(COMPANION_PROMPT_URL),
    "--data-binary",
    shell_quote("@" .. PANEL_PROMPT_PAYLOAD_FILE),
  }, " ")

  local code, out = exec_command(cmd, 9000)
  if code ~= 0 then
    add_line("[ERR] AI prompt request failed (is the API running on 127.0.0.1:8000?)")
    local msg = trim(out)
    if msg ~= "" then add_line(msg) end
    log_prompt_event("ai_error", prompt, msg ~= "" and msg or "request failed")
    return false
  end

  local summary = summarize_prompt_response(out)
  add_line("[AI] " .. summary)
  log_prompt_event("ai", prompt, summary)
  add_ai_response_entry(prompt, out, summary)
  return true
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

local function create_local_track(name)
  if not reaper.InsertTrackAtIndex or not reaper.CountTracks or not reaper.GetTrack then
    add_line("[ERR] REAPER API unavailable: InsertTrackAtIndex")
    return
  end
  local track_name = trim(name)
  local index0 = reaper.CountTracks(0)
  local undo_label = "ReaperGPT: create track"
  if reaper.Undo_BeginBlock then reaper.Undo_BeginBlock() end
  reaper.InsertTrackAtIndex(index0, true)
  local track = reaper.GetTrack(0, index0)
  if not track then
    if reaper.Undo_EndBlock then reaper.Undo_EndBlock(undo_label, -1) end
    add_line("[ERR] failed to create track")
    return
  end

  if track_name ~= "" and reaper.GetSetMediaTrackInfo_String then
    reaper.GetSetMediaTrackInfo_String(track, "P_NAME", track_name, true)
  end

  if reaper.TrackList_AdjustWindows then
    reaper.TrackList_AdjustWindows(false)
  end
  if reaper.UpdateArrange then
    reaper.UpdateArrange()
  end

  if reaper.Undo_EndBlock then reaper.Undo_EndBlock(undo_label, -1) end

  local detail = string.format("created track %d", index0 + 1)
  if track_name ~= "" then
    detail = detail .. string.format(' ("%s")', track_name)
  end
  add_line(detail)
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
  local prompt_text = trim(raw)
  local cmd = prompt_text:lower()
  if cmd == "" then
    return
  end

  if cmd == "play" then
    reaper.OnPlayButton()
    add_line("transport play triggered")
    log_prompt_event("local", raw, "transport.play")
    return
  end

  if cmd == "stop" then
    reaper.OnStopButton()
    add_line("transport stop triggered")
    log_prompt_event("local", raw, "transport.stop")
    return
  end

  local bpm = cmd:match("^tempo%s+([%d%.]+)$")
  if bpm then
    local n = tonumber(bpm)
    if n and n > 0 and reaper.SetCurrentBPM then
      reaper.SetCurrentBPM(0, n, true)
      add_line(string.format("tempo set to %.2f BPM", n))
      log_prompt_event("local", raw, string.format("project.set_tempo bpm=%.2f", n))
    else
      add_line("[ERR] invalid tempo")
      log_prompt_event("local_error", raw, "invalid tempo")
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
  if cmd == "mute" then
    add_line("[PROMPT] please specify a track number, e.g. `mute track 3`")
    return
  end

  track_n = cmd:match("^unmute%s+track%s+(%d+)$")
  if track_n then
    set_track_flag(tonumber(track_n), false, "B_MUTE", "mute")
    return
  end
  if cmd == "unmute" then
    add_line("[PROMPT] please specify a track number, e.g. `unmute track 3`")
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

  local create_track_prefix = cmd:match("^(?:create|add|make)%s+(?:a%s+)?(?:new%s+)?track%s+(?:named|called)%s+")
  local create_track_simple = cmd:match("^(?:create|add|make)%s+(?:a%s+)?(?:new%s+)?track$")
  if create_track_prefix or create_track_simple then
    local name = nil
    if create_track_prefix then
      local start_index = #create_track_prefix + 1
      name = trim(prompt_text:sub(start_index))
    end
    create_local_track(name)
    return
  end

  if cmd:match("^create%s+regions%s+for%s+a%s+.+song$") or cmd == "create regions for a pop song" then
    create_song_form_regions()
    return
  end

  if submit_prompt_to_companion(raw) then
    return
  end

  log_prompt_event("unknown", raw, "no local handler and AI submit failed")
  add_line("[ERR] unknown command: " .. raw)
end

local function loop()
  if first then
    reaper.ImGui_SetNextWindowSize(ctx, 760, 560, reaper.ImGui_Cond_FirstUseEver())
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

    reaper.ImGui_Text(ctx, "Recent AI Responses (click one to inspect/copy)")
    if #ai_responses == 0 then
      reaper.ImGui_TextDisabled(ctx, "No AI responses yet")
    else
      local start_i = math.max(1, #ai_responses - 9)
      for i = #ai_responses, 1, -1 do
        if i < start_i then break end
        local entry = ai_responses[i]
        local label = string.format(
          "%s  %s  |  %s",
          entry.timestamp or "",
          clip_text(entry.prompt, 32),
          clip_text(entry.summary, 52)
        )
        local selected = (ai_selected_index == i)
        local clicked = false
        if reaper.ImGui_Selectable then
          clicked = reaper.ImGui_Selectable(ctx, label, selected)
        else
          reaper.ImGui_TextWrapped(ctx, label)
        end
        if clicked then
          ai_selected_index = i
        end
      end
    end

    local selected_entry = ai_selected_index and ai_responses[ai_selected_index] or nil
    if selected_entry then
      reaper.ImGui_TextWrapped(ctx, "Selected prompt: " .. selected_entry.prompt)
      if reaper.ImGui_Button(ctx, "Copy Selected Response JSON") then
        if copy_to_clipboard(selected_entry.raw) then
          add_line("[AI] copied selected response JSON to clipboard")
        else
          add_line("[ERR] clipboard copy unavailable (need ReaImGui clipboard support or SWS)")
        end
      end
      reaper.ImGui_SameLine(ctx)
      if reaper.ImGui_Button(ctx, "Copy Selected Prompt") then
        if copy_to_clipboard(selected_entry.prompt) then
          add_line("[AI] copied selected prompt to clipboard")
        else
          add_line("[ERR] clipboard copy unavailable (need ReaImGui clipboard support or SWS)")
        end
      end
      local preview = selected_entry.raw or ""
      reaper.ImGui_TextWrapped(ctx, clip_text(preview, 800))
      if reaper.ImGui_IsItemHovered and reaper.ImGui_SetTooltip then
        reaper.ImGui_SetTooltip(ctx, "Use Copy Selected Response JSON to paste into chat quickly")
      end
    end

    reaper.ImGui_Separator(ctx)

    local submitted
    submitted, input_text = reaper.ImGui_InputText(ctx, "Prompt", input_text,
      reaper.ImGui_InputTextFlags_EnterReturnsTrue())

    if reaper.ImGui_IsItemActive and reaper.ImGui_IsItemActive(ctx) and #prompt_history > 0 then
      if is_key_pressed(reaper.ImGui_Key_UpArrow) then
        if prompt_history_index == nil then
          prompt_history_draft = input_text or ""
          prompt_history_index = #prompt_history
        elseif prompt_history_index > 1 then
          prompt_history_index = prompt_history_index - 1
        end
        input_text = prompt_history[prompt_history_index] or input_text
      elseif is_key_pressed(reaper.ImGui_Key_DownArrow) then
        if prompt_history_index ~= nil then
          if prompt_history_index < #prompt_history then
            prompt_history_index = prompt_history_index + 1
            input_text = prompt_history[prompt_history_index] or input_text
          else
            prompt_history_index = nil
            input_text = prompt_history_draft or ""
          end
        end
      end
    end

    reaper.ImGui_SameLine(ctx)
    local clicked = reaper.ImGui_Button(ctx, "Send")

    if submitted or clicked then
      local t = input_text
      input_text = ""
      if t ~= "" then
        add_history_entry(t)
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
