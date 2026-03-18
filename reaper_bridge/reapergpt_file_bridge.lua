local function normalize_path(path)
  local normalized = path:gsub("/", "\\")
  local prefix = normalized:match("^%a:[\\]") or normalized:match("^\\\\[^\\]+\\[^\\]+\\")
  local is_absolute = prefix ~= nil
  local body = normalized
  if prefix then
    body = normalized:sub(#prefix + 1)
  end
  local parts = {}
  for part in body:gmatch("[^\\]+") do
    if part == ".." then
      if #parts > 0 then
        table.remove(parts)
      end
    elseif part ~= "." and part ~= "" then
      parts[#parts + 1] = part
    end
  end
  local joined = table.concat(parts, "\\")
  if is_absolute and prefix then
    if joined == "" then
      return prefix:gsub("\\$", "")
    end
    return prefix .. joined
  end
  return joined
end

local SCRIPT_DIR = normalize_path(debug.getinfo(1, "S").source:match("@?(.*[\\/])") or ".\\")
local REPO_ROOT = normalize_path(SCRIPT_DIR .. "\\..\\")
local BRIDGE_DIR = normalize_path(REPO_ROOT .. "\\data\\reaper_bridge\\") .. "\\"
local REQUEST_PATH = BRIDGE_DIR .. "pending_plan.json"
local RESULT_PATH = BRIDGE_DIR .. "execution_result.json"
local STATE_PATH = BRIDGE_DIR .. "project_state.json"
local LOG_PATH = normalize_path(REPO_ROOT .. "\\data\\reaper_bridge_debug.log")

local COLOR_MAP = {
  red = {255, 80, 80},
  orange = {255, 160, 70},
  yellow = {240, 215, 60},
  green = {80, 200, 120},
  blue = {70, 140, 255},
  purple = {155, 100, 255},
  pink = {255, 100, 190},
  white = {245, 245, 245},
  black = {20, 20, 20},
}

local json = {}

local function append_log(message)
  local stamp = os.date("%Y-%m-%d %H:%M:%S")
  local handle = io.open(LOG_PATH, "a")
  if handle then
    handle:write(string.format("[%s] %s\n", stamp, tostring(message)))
    handle:close()
  end
end

local function json_error(message, position)
  error(string.format("JSON parse error at %d: %s", position or -1, message))
end

local function skip_ws(text, index)
  local length = #text
  while index <= length do
    local char = text:sub(index, index)
    if char ~= " " and char ~= "\n" and char ~= "\r" and char ~= "\t" then
      break
    end
    index = index + 1
  end
  return index
end

local function parse_string(text, index)
  index = index + 1
  local parts = {}
  while index <= #text do
    local char = text:sub(index, index)
    if char == "\"" then
      return table.concat(parts), index + 1
    end
    if char == "\\" then
      local escaped = text:sub(index + 1, index + 1)
      local map = {
        ["\\"] = "\\",
        ["\""] = "\"",
        ["/"] = "/",
        b = "\b",
        f = "\f",
        n = "\n",
        r = "\r",
        t = "\t",
      }
      if escaped == "u" then
        local hex = text:sub(index + 2, index + 5)
        if #hex ~= 4 or not hex:match("^%x%x%x%x$") then
          json_error("invalid unicode escape", index)
        end
        parts[#parts + 1] = utf8.char(tonumber(hex, 16))
        index = index + 6
      elseif map[escaped] then
        parts[#parts + 1] = map[escaped]
        index = index + 2
      else
        json_error("invalid escape", index)
      end
    else
      parts[#parts + 1] = char
      index = index + 1
    end
  end
  json_error("unterminated string", index)
end

local parse_value

local function parse_array(text, index)
  local result = {}
  index = skip_ws(text, index + 1)
  if text:sub(index, index) == "]" then
    return result, index + 1
  end
  while index <= #text do
    local value
    value, index = parse_value(text, index)
    result[#result + 1] = value
    index = skip_ws(text, index)
    local char = text:sub(index, index)
    if char == "]" then
      return result, index + 1
    end
    if char ~= "," then
      json_error("expected ',' or ']'", index)
    end
    index = skip_ws(text, index + 1)
  end
  json_error("unterminated array", index)
end

local function parse_object(text, index)
  local result = {}
  index = skip_ws(text, index + 1)
  if text:sub(index, index) == "}" then
    return result, index + 1
  end
  while index <= #text do
    if text:sub(index, index) ~= "\"" then
      json_error("expected string key", index)
    end
    local key
    key, index = parse_string(text, index)
    index = skip_ws(text, index)
    if text:sub(index, index) ~= ":" then
      json_error("expected ':'", index)
    end
    index = skip_ws(text, index + 1)
    local value
    value, index = parse_value(text, index)
    result[key] = value
    index = skip_ws(text, index)
    local char = text:sub(index, index)
    if char == "}" then
      return result, index + 1
    end
    if char ~= "," then
      json_error("expected ',' or '}'", index)
    end
    index = skip_ws(text, index + 1)
  end
  json_error("unterminated object", index)
end

local function parse_number(text, index)
  local start_index = index
  local pattern = "^%-?%d+%.?%d*[eE]?[%+%-]?%d*"
  local chunk = text:sub(index)
  local raw = chunk:match(pattern)
  if not raw or raw == "" then
    json_error("invalid number", index)
  end
  local value = tonumber(raw)
  if value == nil then
    json_error("invalid number", index)
  end
  return value, start_index + #raw
end

parse_value = function(text, index)
  index = skip_ws(text, index)
  local char = text:sub(index, index)
  if char == "\"" then
    return parse_string(text, index)
  end
  if char == "{" then
    return parse_object(text, index)
  end
  if char == "[" then
    return parse_array(text, index)
  end
  if char == "-" or char:match("%d") then
    return parse_number(text, index)
  end
  if text:sub(index, index + 3) == "true" then
    return true, index + 4
  end
  if text:sub(index, index + 4) == "false" then
    return false, index + 5
  end
  if text:sub(index, index + 3) == "null" then
    return nil, index + 4
  end
  json_error("unexpected token", index)
end

function json.decode(text)
  local value, index = parse_value(text, 1)
  index = skip_ws(text, index)
  if index <= #text then
    json_error("trailing data", index)
  end
  return value
end

local function escape_string(value)
  local replacements = {
    ["\\"] = "\\\\",
    ["\""] = "\\\"",
    ["\b"] = "\\b",
    ["\f"] = "\\f",
    ["\n"] = "\\n",
    ["\r"] = "\\r",
    ["\t"] = "\\t",
  }
  return value:gsub("[\\\"\b\f\n\r\t]", replacements)
end

local function is_array(value)
  local count = 0
  for key, _ in pairs(value) do
    if type(key) ~= "number" then
      return false
    end
    count = count + 1
  end
  return count == #value
end

function json.encode(value)
  local value_type = type(value)
  if value_type == "nil" then
    return "null"
  end
  if value_type == "boolean" then
    return value and "true" or "false"
  end
  if value_type == "number" then
    return tostring(value)
  end
  if value_type == "string" then
    return "\"" .. escape_string(value) .. "\""
  end
  if value_type == "table" then
    if is_array(value) then
      local items = {}
      for index = 1, #value do
        items[#items + 1] = json.encode(value[index])
      end
      return "[" .. table.concat(items, ",") .. "]"
    end
    local items = {}
    for key, item in pairs(value) do
      items[#items + 1] = json.encode(tostring(key)) .. ":" .. json.encode(item)
    end
    return "{" .. table.concat(items, ",") .. "}"
  end
  error("cannot encode type " .. value_type)
end

local function ensure_dir(path)
  return reaper.RecursiveCreateDirectory(path, 0)
end

local function read_file(path)
  local handle = io.open(path, "r")
  if not handle then
    return nil
  end
  local content = handle:read("*a")
  handle:close()
  return content
end

local function write_file(path, content)
  local handle, err = io.open(path, "w")
  if not handle then
    append_log("write_file failed for " .. path .. ": " .. tostring(err))
    error("unable to open file for write: " .. path .. " (" .. tostring(err) .. ")")
  end
  handle:write(content)
  handle:close()
  append_log("write_file ok: " .. path)
end

local function write_json(path, payload)
  write_file(path, json.encode(payload))
end

local function get_track_by_ref(ref)
  if type(ref) ~= "table" then
    return nil, "track reference must be an object"
  end
  local ref_type = ref.type
  local value = ref.value
  if ref_type == "track_id" or ref_type == "track_index" then
    local index = tonumber(value)
    if not index or index < 1 then
      return nil, "invalid track index"
    end
    local track = reaper.GetTrack(0, index - 1)
    if not track then
      return nil, "track not found"
    end
    return track
  end
  if ref_type == "track_name" and type(value) == "string" then
    local track_count = reaper.CountTracks(0)
    for index = 0, track_count - 1 do
      local track = reaper.GetTrack(0, index)
      local _, name = reaper.GetSetMediaTrackInfo_String(track, "P_NAME", "", false)
      if name:lower() == value:lower() then
        return track
      end
    end
    return nil, "track not found"
  end
  return nil, "unsupported track reference"
end

local function create_track(args, is_bus)
  local track_count = reaper.CountTracks(0)
  local insert_index = track_count
  reaper.InsertTrackAtIndex(insert_index, true)
  local track = reaper.GetTrack(0, insert_index)
  local name = args.name or (is_bus and ("Bus " .. tostring(insert_index + 1)) or ("Track " .. tostring(insert_index + 1)))
  reaper.GetSetMediaTrackInfo_String(track, "P_NAME", name, true)
  return {
    track_id = insert_index + 1,
    name = name,
    is_bus = is_bus,
  }
end

local function create_send(args)
  local src, src_error = get_track_by_ref(args.src)
  if not src then
    error(src_error)
  end
  local dst, dst_error = get_track_by_ref(args.dst)
  if not dst then
    error(dst_error)
  end
  local send_index = reaper.CreateTrackSend(src, dst)
  return {
    send_index = send_index,
  }
end

local function insert_fx(args)
  local track, track_error = get_track_by_ref(args.track_ref)
  if not track then
    error(track_error)
  end
  local fx_name = args.fx_name
  if type(fx_name) ~= "string" or fx_name == "" then
    error("fx_name is required")
  end
  local fx_index = reaper.TrackFX_AddByName(track, fx_name, false, -1)
  if fx_index < 0 then
    error("fx not found: " .. fx_name)
  end
  return {
    fx_index = fx_index,
    fx_name = fx_name,
  }
end

local function set_track_color(args)
  local color_name = args.color
  local rgb = COLOR_MAP[type(color_name) == "string" and color_name:lower() or ""]
  if not rgb then
    error("unsupported color")
  end
  local track, track_error = get_track_by_ref(args.track_ref)
  if not track and type(args.track_index) == "number" then
    track, track_error = get_track_by_ref({type = "track_index", value = args.track_index})
  end
  if not track then
    error(track_error or "track not found")
  end
  local native = reaper.ColorToNative(rgb[1], rgb[2], rgb[3]) | 0x1000000
  reaper.SetTrackColor(track, native)
  return {color = color_name}
end

local function set_tempo(args)
  local bpm = tonumber(args.bpm)
  if not bpm then
    error("bpm is required")
  end
  reaper.SetCurrentBPM(0, bpm, true)
  return {tempo = bpm}
end

local TOOL_MAP = {
  create_track = function(args) return create_track(args, false) end,
  create_bus = function(args) return create_track(args, true) end,
  create_send = create_send,
  insert_fx = insert_fx,
  set_track_color = set_track_color,
  ["project.set_tempo"] = set_tempo,
}

local function track_id(track)
  if not track then
    return nil
  end
  return math.floor(reaper.GetMediaTrackInfo_Value(track, "IP_TRACKNUMBER"))
end

local function track_name(track)
  if not track then
    return ""
  end
  local _, name = reaper.GetSetMediaTrackInfo_String(track, "P_NAME", "", false)
  return name
end

local function collect_markers_and_regions()
  local markers = {}
  local regions = {}
  local _, marker_count, region_count = reaper.CountProjectMarkers(0)
  local total = marker_count + region_count
  for index = 0, total - 1 do
    local _, is_region, position, region_end, name, number = reaper.EnumProjectMarkers(index)
    local entry = {
      id = number,
      name = name or "",
      start = position,
    }
    if is_region then
      entry["end"] = region_end
      regions[#regions + 1] = entry
    else
      markers[#markers + 1] = entry
    end
  end
  return markers, regions
end

local function collect_selected_items()
  local items = {}
  local selected_count = reaper.CountSelectedMediaItems(0)
  for index = 0, selected_count - 1 do
    local item = reaper.GetSelectedMediaItem(0, index)
    local item_track = reaper.GetMediaItemTrack(item)
    local active_take = reaper.GetActiveTake(item)
    local take_name = ""
    if active_take then
      take_name = reaper.GetTakeName(active_take) or ""
    end
    items[#items + 1] = {
      index = index + 1,
      position = reaper.GetMediaItemInfo_Value(item, "D_POSITION"),
      length = reaper.GetMediaItemInfo_Value(item, "D_LENGTH"),
      track_id = track_id(item_track),
      track_name = track_name(item_track),
      take_name = take_name,
    }
  end
  return items
end

local function collect_state()
  local tracks = {}
  local sends = {}
  local receives = {}
  local selected_tracks = {}
  local selected_track_ids = {}
  local folder_structure = {}
  local track_count = reaper.CountTracks(0)
  local folder_stack = {}
  for index = 0, track_count - 1 do
    local track = reaper.GetTrack(0, index)
    local id = index + 1
    local name = track_name(track)
    local fx = {}
    local fx_count = reaper.TrackFX_GetCount(track)
    for fx_index = 0, fx_count - 1 do
      local _, fx_name = reaper.TrackFX_GetFXName(track, fx_index, "")
      fx[#fx + 1] = fx_name
    end
    local parent_id = folder_stack[#folder_stack]
    local folder_delta = math.floor(reaper.GetMediaTrackInfo_Value(track, "I_FOLDERDEPTH"))
    local depth = #folder_stack
    local mainsend = reaper.GetMediaTrackInfo_Value(track, "B_MAINSEND") > 0
    local selected = reaper.IsTrackSelected(track)
    local track_sends = {}
    local track_receives = {}
    local send_count = reaper.GetTrackNumSends(track, 0)
    for send_index = 0, send_count - 1 do
      local dest_track = reaper.GetTrackSendInfo_Value(track, 0, send_index, "P_DESTTRACK")
      local send_info = {
        index = send_index,
        src = id,
        dst = track_id(dest_track),
        dst_name = track_name(dest_track),
      }
      sends[#sends + 1] = send_info
      track_sends[#track_sends + 1] = send_info
    end
    local receive_count = reaper.GetTrackNumSends(track, -1)
    for receive_index = 0, receive_count - 1 do
      local src_track = reaper.GetTrackSendInfo_Value(track, -1, receive_index, "P_SRCTRACK")
      local receive_info = {
        index = receive_index,
        src = track_id(src_track),
        src_name = track_name(src_track),
        dst = id,
      }
      receives[#receives + 1] = receive_info
      track_receives[#track_receives + 1] = receive_info
    end

    local track_entry = {
      id = id,
      name = name,
      fx = fx,
      fx_count = fx_count,
      color = reaper.GetTrackColor(track),
      selected = selected,
      sends = track_sends,
      receives = track_receives,
      depth = depth,
      parent_track_id = parent_id,
      folder_depth_delta = folder_delta,
      is_folder_parent = folder_delta > 0,
      has_parent_send = mainsend,
      is_bus = #track_receives > 0,
    }

    if selected then
      selected_track_ids[#selected_track_ids + 1] = id
      selected_tracks[#selected_tracks + 1] = {
        id = id,
        name = name,
      }
    end

    tracks[#tracks + 1] = track_entry

    folder_structure[#folder_structure + 1] = {
      id = id,
      name = name,
      parent_track_id = parent_id,
      depth = depth,
      is_folder_parent = folder_delta > 0,
      is_bus = track_entry.is_bus,
    }

    if folder_delta > 0 then
      folder_stack[#folder_stack + 1] = id
    elseif folder_delta < 0 then
      for _ = 1, math.abs(folder_delta) do
        if #folder_stack > 0 then
          table.remove(folder_stack)
        end
      end
    end
  end
  local _, project_name = reaper.GetProjectName(0, "")
  local markers, regions = collect_markers_and_regions()
  local selected_items = collect_selected_items()
  return {
    bridge_connected = true,
    project_name = project_name ~= "" and project_name or "REAPER Project",
    tempo = reaper.Master_GetTempo(),
    tracks = tracks,
    sends = sends,
    receives = receives,
    markers = markers,
    regions = regions,
    selected_track_ids = selected_track_ids,
    selected_item_count = #selected_items,
    folder_structure = folder_structure,
    selection = {
      tracks = selected_tracks,
      items = selected_items,
    },
    envelopes_summary = {},
  }
end

local function execute_plan(request)
  local results = {}
  reaper.Undo_BeginBlock()
  for index, step in ipairs(request.steps or {}) do
    local tool = step.tool
    local handler = TOOL_MAP[tool]
    if not handler then
      reaper.Undo_EndBlock("Reaper Agent Failed", -1)
      return {
        request_id = request.request_id,
        status = "error",
        error = "unsupported tool: " .. tostring(tool),
        results = results,
      }
    end
    local ok, output = pcall(handler, step.args or {})
    if not ok then
      results[#results + 1] = {
        index = index - 1,
        tool = tool,
        status = "rejected",
        detail = tostring(output),
      }
      reaper.Undo_EndBlock("Reaper Agent Failed", -1)
      return {
        request_id = request.request_id,
        status = "error",
        error = tostring(output),
        results = results,
      }
    end
    results[#results + 1] = {
      index = index - 1,
      tool = tool,
      status = "accepted",
      output = output,
    }
  end
  reaper.Undo_EndBlock("Reaper Agent Action", -1)
  return {
    request_id = request.request_id,
    status = "ok",
    results = results,
  }
end

local last_request_id = nil

local function process_request()
  ensure_dir(BRIDGE_DIR)
  local raw = read_file(REQUEST_PATH)
  if not raw or raw == "" then
    return
  end
  append_log("request file detected")
  local ok, request = pcall(json.decode, raw)
  if not ok then
    append_log("json decode failed: " .. tostring(request))
    write_json(RESULT_PATH, {
      request_id = "unknown",
      status = "error",
      error = tostring(request),
      results = {},
    })
    os.remove(REQUEST_PATH)
    return
  end
  if request.request_id == last_request_id then
    append_log("duplicate request ignored: " .. tostring(request.request_id))
    return
  end
  last_request_id = request.request_id
  append_log("executing request: " .. tostring(request.request_id))
  local result = execute_plan(request)
  write_json(RESULT_PATH, result)
  write_json(STATE_PATH, collect_state())
  os.remove(REQUEST_PATH)
  append_log("request completed: " .. tostring(request.request_id))
end

local function loop()
  local ok, err = pcall(process_request)
  if not ok then
    append_log("loop error: " .. tostring(err))
    write_json(RESULT_PATH, {
      request_id = last_request_id or "unknown",
      status = "error",
      error = tostring(err),
      results = {},
    })
  end
  reaper.defer(loop)
end

ensure_dir(BRIDGE_DIR)
append_log("script_dir=" .. SCRIPT_DIR)
append_log("repo_root=" .. REPO_ROOT)
append_log("bridge_dir=" .. BRIDGE_DIR)
write_json(STATE_PATH, collect_state())
append_log("initial state written")
loop()
