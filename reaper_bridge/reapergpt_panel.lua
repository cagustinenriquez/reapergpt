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
local DATA_DIR = normalize_path(REPO_ROOT .. "\\data\\reaper_panel") .. "\\"
local PANEL_LOG_PATH = normalize_path(REPO_ROOT .. "\\data\\reaper_panel.log")
local ACTIVE_INSTANCE_PATH = DATA_DIR .. "active_instance.json"
local API_BASE_URL = "http://127.0.0.1:8000"
local PANEL_INSTANCE_STALE_SECONDS = 5
math.randomseed(os.time())
local PANEL_INSTANCE_ID = string.format("%d-%06d", os.time(), math.random(0, 999999))

local state = {
  prompt = "",
  plan_id = nil,
  plan_summary = "No preview loaded.",
  plan_steps = {},
  clarification_id = nil,
  clarification_question = nil,
  clarification_options = {},
  clarification_answers = {},
  last_result = "No request sent yet.",
  project_summary = "Unknown project state.",
  project_detail_lines = {},
  apply_summary = "No apply result yet.",
  apply_result_lines = {},
  verification_summary = "No verification run yet.",
  verification_result_lines = {},
  mismatch_lines = {},
  apply_pending = false,
  instance_warning = nil,
  instance_read_only = false,
}

local json = {}
local mouse = {down = false}
local update_project_summary
local append_line
local summarize_project
local format_step_result_line
local format_verification_line
local draw_text_lines

local function append_log(message)
  reaper.RecursiveCreateDirectory(DATA_DIR, 0)
  local handle = io.open(PANEL_LOG_PATH, "a")
  if handle then
    handle:write(string.format("[%s] %s\n", os.date("%Y-%m-%d %H:%M:%S"), tostring(message)))
    handle:close()
  end
end

local function skip_ws(text, index)
  while index <= #text do
    local char = text:sub(index, index)
    if char ~= " " and char ~= "\n" and char ~= "\r" and char ~= "\t" then
      break
    end
    index = index + 1
  end
  return index
end

local function json_error(message, position)
  error(string.format("JSON parse error at %d: %s", position or -1, message))
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
  local raw = text:sub(index):match("^%-?%d+%.?%d*[eE]?[%+%-]?%d*")
  if not raw or raw == "" then
    json_error("invalid number", index)
  end
  local value = tonumber(raw)
  if value == nil then
    json_error("invalid number", index)
  end
  return value, index + #raw
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

local function write_file(path, content)
  reaper.RecursiveCreateDirectory(DATA_DIR, 0)
  local handle, err = io.open(path, "w")
  if not handle then
    error("unable to write " .. path .. ": " .. tostring(err))
  end
  handle:write(content)
  handle:close()
end

local function read_file(path)
  local handle = io.open(path, "r")
  if not handle then
    return nil
  end
  local content = handle:read("*a")
  handle:close()
  if content and content:sub(1, 3) == "\239\187\191" then
    content = content:sub(4)
  end
  return content
end

local function delete_file(path)
  os.remove(path)
end

local function read_json_file(path)
  local body = read_file(path)
  if not body or body == "" then
    return nil
  end
  local ok, payload = pcall(json.decode, body)
  if not ok or type(payload) ~= "table" then
    return nil
  end
  return payload
end

local function write_active_instance()
  local existing = read_json_file(ACTIVE_INSTANCE_PATH)
  local now = os.time()
  if existing and existing.instance_id and existing.instance_id ~= PANEL_INSTANCE_ID and (now - tonumber(existing.last_heartbeat or 0)) <= PANEL_INSTANCE_STALE_SECONDS then
    state.instance_warning = "Another panel instance was already active: " .. tostring(existing.instance_id)
    state.instance_read_only = true
    append_log("panel_duplicate_detected current=" .. PANEL_INSTANCE_ID .. " existing=" .. tostring(existing.instance_id))
    return
  end
  write_file(
    ACTIVE_INSTANCE_PATH,
    json.encode({
      instance_id = PANEL_INSTANCE_ID,
      started_at = os.date("%Y-%m-%d %H:%M:%S"),
      last_heartbeat = now,
      script = "reapergpt_panel.lua",
    })
  )
  append_log("panel_start instance=" .. PANEL_INSTANCE_ID)
end

local function heartbeat_active_instance()
  if state.instance_read_only then
    return
  end
  write_file(
    ACTIVE_INSTANCE_PATH,
    json.encode({
      instance_id = PANEL_INSTANCE_ID,
      started_at = os.date("%Y-%m-%d %H:%M:%S"),
      last_heartbeat = os.time(),
      script = "reapergpt_panel.lua",
    })
  )
end

local function clear_active_instance()
  local active = read_json_file(ACTIVE_INSTANCE_PATH)
  if active and active.instance_id == PANEL_INSTANCE_ID then
    delete_file(ACTIVE_INSTANCE_PATH)
  end
  append_log("panel_stop instance=" .. PANEL_INSTANCE_ID)
end

local function shell_quote(value)
  return "\"" .. tostring(value):gsub("\"", "\\\"") .. "\""
end

local function powershell_quote(value)
  return "'" .. tostring(value):gsub("'", "''") .. "'"
end

local function api_request(method, route, payload)
  reaper.RecursiveCreateDirectory(DATA_DIR, 0)
  local request_path = DATA_DIR .. "panel_request.json"
  local response_path = DATA_DIR .. "panel_response.json"
  if payload ~= nil then
    write_file(request_path, json.encode(payload))
  end
  local script_lines = {
    "$ErrorActionPreference = 'Stop'",
    "$uri = " .. powershell_quote(API_BASE_URL .. route),
    "$responsePath = " .. powershell_quote(response_path),
    "$requestBody = $null",
    "$utf8NoBom = New-Object System.Text.UTF8Encoding($false)",
  }
  if payload ~= nil then
    script_lines[#script_lines + 1] = "$requestBody = Get-Content -Raw -Path " .. powershell_quote(request_path)
  end
  script_lines[#script_lines + 1] = "try {"
  if payload ~= nil then
    script_lines[#script_lines + 1] = "  $resp = Invoke-WebRequest -UseBasicParsing -Method " .. powershell_quote(method) .. " -Uri $uri -ContentType 'application/json' -Body $requestBody"
  else
    script_lines[#script_lines + 1] = "  $resp = Invoke-WebRequest -UseBasicParsing -Method " .. powershell_quote(method) .. " -Uri $uri"
  end
  script_lines[#script_lines + 1] = "  [System.IO.File]::WriteAllText($responsePath, $resp.Content, $utf8NoBom)"
  script_lines[#script_lines + 1] = "  Write-Output ('HTTPSTATUS:' + [int]$resp.StatusCode)"
  script_lines[#script_lines + 1] = "} catch {"
  script_lines[#script_lines + 1] = "  if ($_.Exception.Response) {"
    script_lines[#script_lines + 1] = "    $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())"
    script_lines[#script_lines + 1] = "    $content = $reader.ReadToEnd()"
    script_lines[#script_lines + 1] = "    [System.IO.File]::WriteAllText($responsePath, $content, $utf8NoBom)"
    script_lines[#script_lines + 1] = "    Write-Output ('HTTPSTATUS:' + [int]$_.Exception.Response.StatusCode.value__)"
    script_lines[#script_lines + 1] = "    exit 1"
  script_lines[#script_lines + 1] = "  }"
  script_lines[#script_lines + 1] = "  Write-Output ('ERROR:' + $_.Exception.Message)"
  script_lines[#script_lines + 1] = "  exit 1"
  script_lines[#script_lines + 1] = "}"
  local ps_command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command " .. shell_quote(table.concat(script_lines, "; "))
  local exec_result, output = reaper.ExecProcess(ps_command, 10000)
  local exec_text = tostring(exec_result or "")
  local output_text = tostring(output or "")
  local combined_text = exec_text
  if output_text ~= "" then
    combined_text = combined_text .. "\n" .. output_text
  end
  local exit_code = tonumber(exec_text:match("^%-?%d+")) or tonumber(exec_result) or 0
  append_log("api_request " .. method .. " " .. route .. " exec=" .. exec_text .. " output=" .. output_text)
  local body = read_file(response_path) or ""
  local http_status = combined_text:match("HTTPSTATUS:(%d+)")
  local error_message = combined_text:match("ERROR:(.+)")
  append_log(
    "api_request_result route="
      .. route
      .. " exit="
      .. tostring(exit_code)
      .. " http_status="
      .. tostring(http_status)
      .. " body_len="
      .. tostring(#body)
  )
  if exit_code ~= 0 and not http_status then
    append_log("api_request_branch route=" .. route .. " branch=transport_error")
    if error_message and error_message ~= "" then
      return false, error_message
    end
    return false, "API request failed. Confirm the FastAPI server is running on " .. API_BASE_URL
  end
  local ok, parsed = pcall(json.decode, body)
  if tonumber(http_status or "0") and tonumber(http_status) >= 400 then
    append_log("api_request_branch route=" .. route .. " branch=http_error")
    if ok and type(parsed) == "table" then
      return false, parsed
    end
    return false, body ~= "" and body or ("HTTP " .. tostring(http_status))
  end
  if not ok then
    append_log("api_request parse_failed route=" .. route .. " body=" .. tostring(body))
    return false, "invalid API response: " .. tostring(parsed)
  end
  append_log("api_request_branch route=" .. route .. " branch=success")
  return true, parsed
end

local APPLY_REQUEST_PATH = DATA_DIR .. "apply_request.json"
local APPLY_RESPONSE_PATH = DATA_DIR .. "apply_response.json"
local APPLY_STATUS_PATH = DATA_DIR .. "apply_status.json"
local APPLY_SCRIPT_PATH = DATA_DIR .. "apply_request.ps1"

local function start_apply_request(plan_id)
  delete_file(APPLY_RESPONSE_PATH)
  delete_file(APPLY_STATUS_PATH)
  write_file(APPLY_REQUEST_PATH, json.encode({plan_id = plan_id}))
  local script_lines = {
    "$ErrorActionPreference = 'Stop'",
    "$uri = " .. powershell_quote(API_BASE_URL .. "/execute-plan"),
    "$requestPath = " .. powershell_quote(APPLY_REQUEST_PATH),
    "$responsePath = " .. powershell_quote(APPLY_RESPONSE_PATH),
    "$statusPath = " .. powershell_quote(APPLY_STATUS_PATH),
    "$utf8NoBom = New-Object System.Text.UTF8Encoding($false)",
    "try {",
    "  $requestBody = Get-Content -Raw -Path $requestPath",
    "  $resp = Invoke-WebRequest -UseBasicParsing -Method 'POST' -Uri $uri -ContentType 'application/json' -Body $requestBody",
    "  [System.IO.File]::WriteAllText($responsePath, $resp.Content, $utf8NoBom)",
    "  [System.IO.File]::WriteAllText($statusPath, (([pscustomobject]@{state='ok'; http_status=[int]$resp.StatusCode} | ConvertTo-Json -Compress)), $utf8NoBom)",
    "} catch {",
    "  if ($_.Exception.Response) {",
    "    $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())",
    "    $content = $reader.ReadToEnd()",
    "    [System.IO.File]::WriteAllText($responsePath, $content, $utf8NoBom)",
    "    [System.IO.File]::WriteAllText($statusPath, (([pscustomobject]@{state='error'; http_status=[int]$_.Exception.Response.StatusCode.value__; message='HTTP request failed'} | ConvertTo-Json -Compress)), $utf8NoBom)",
    "    exit 1",
    "  }",
    "  [System.IO.File]::WriteAllText($statusPath, (([pscustomobject]@{state='error'; message=$_.Exception.Message} | ConvertTo-Json -Compress)), $utf8NoBom)",
    "  exit 1",
    "}",
  }
  write_file(APPLY_SCRIPT_PATH, table.concat(script_lines, "\r\n"))
  local command = "cmd.exe /c start \"\" /b powershell.exe -NoProfile -ExecutionPolicy Bypass -File " .. shell_quote(APPLY_SCRIPT_PATH)
  append_log("start_apply_request command=" .. command)
  local exec_result = os.execute(command)
  append_log("start_apply_request result=" .. tostring(exec_result))
  return exec_result ~= nil and exec_result ~= false
end

local function poll_apply_result()
  if not state.apply_pending then
    return
  end
  local status_body = read_file(APPLY_STATUS_PATH)
  if not status_body or status_body == "" then
    return
  end
  local ok, status_payload = pcall(json.decode, status_body)
  if not ok or type(status_payload) ~= "table" then
    state.apply_pending = false
    state.last_result = "Apply failed: invalid async status file."
    state.apply_summary = "Apply failed."
    state.apply_result_lines = {"Invalid async status file."}
    state.verification_summary = "Verification unavailable."
    state.verification_result_lines = {}
    state.mismatch_lines = {}
    append_log("poll_apply_result invalid_status=" .. tostring(status_body))
    return
  end
  state.apply_pending = false
  if status_payload.state ~= "ok" then
    local message = status_payload.message or ("HTTP " .. tostring(status_payload.http_status or "?"))
    local response_body = read_file(APPLY_RESPONSE_PATH)
    if response_body and response_body ~= "" then
      local parsed_ok, parsed = pcall(json.decode, response_body)
      if parsed_ok and type(parsed) == "table" and parsed.detail then
        message = parsed.detail
      end
    end
    state.last_result = "Apply failed: " .. tostring(message)
    state.apply_summary = "Apply failed."
    state.apply_result_lines = {tostring(message)}
    state.verification_summary = "Verification unavailable."
    state.verification_result_lines = {}
    state.mismatch_lines = {}
    append_log("poll_apply_result error=" .. tostring(message))
    return
  end
  local response_body = read_file(APPLY_RESPONSE_PATH) or ""
  local parsed_ok, payload = pcall(json.decode, response_body)
  if not parsed_ok or type(payload) ~= "table" then
    state.last_result = "Apply failed: invalid API response."
    state.apply_summary = "Apply failed."
    state.apply_result_lines = {"Invalid API response."}
    state.verification_summary = "Verification unavailable."
    state.verification_result_lines = {}
    state.mismatch_lines = {}
    append_log("poll_apply_result invalid_response=" .. tostring(response_body))
    return
  end
  local results = payload.results or {}
  local success = payload.success ~= false
  local verification_passed = payload.verification_passed == true
  local verification_results = payload.verification_results or {}
  local verification_errors = payload.verification_errors or {}

  state.apply_result_lines = {}
  for _, result in ipairs(results) do
    append_line(state.apply_result_lines, format_step_result_line(result))
  end
  if #state.apply_result_lines == 0 then
    append_line(state.apply_result_lines, "No per-step results returned.")
  end

  state.verification_result_lines = {}
  for _, item in ipairs(verification_results) do
    append_line(state.verification_result_lines, format_verification_line(item))
  end
  if #state.verification_result_lines == 0 then
    append_line(state.verification_result_lines, "No verification checks returned.")
  end

  state.mismatch_lines = {}
  for _, item in ipairs(verification_errors) do
    append_line(state.mismatch_lines, item)
  end

  if success then
    state.last_result = string.format("Apply finished. Steps executed: %d. Verification: %s", #results, verification_passed and "passed" or "failed")
    state.apply_summary = string.format("Bridge executed %d step(s) successfully.", #results)
  else
    state.last_result = tostring(payload.project_state_error or "Apply failed.")
    state.apply_summary = string.format("Apply returned %d step result(s).", #results)
  end
  state.verification_summary = string.format(
    "Verification %s. Checks: %d  Mismatches: %d",
    verification_passed and "passed" or "failed",
    #verification_results,
    #verification_errors
  )
  append_log("poll_apply_result success=" .. tostring(success) .. " steps=" .. tostring(#results))
  if payload.final_project_state and type(payload.final_project_state) == "table" then
    summarize_project(payload.final_project_state)
  else
    update_project_summary()
  end
end

local function format_step(step)
  local tool = step.tool or "unknown"
  local args = step.args or {}
  if tool == "create_track" or tool == "create_bus" then
    return tool .. " " .. tostring(args.name or "")
  end
  if tool == "create_send" then
    local src = args.src and args.src.value or "?"
    local dst = args.dst and args.dst.value or "?"
    return tool .. " " .. tostring(src) .. " -> " .. tostring(dst)
  end
  if tool == "insert_fx" then
    local target = args.track_ref and args.track_ref.value or "?"
    return tool .. " " .. tostring(args.fx_name or "") .. " on " .. tostring(target)
  end
  if tool == "project.set_tempo" then
    return tool .. " " .. tostring(args.bpm or "")
  end
  return tool
end

local function clear_clarification()
  state.clarification_id = nil
  state.clarification_question = nil
  state.clarification_options = {}
end

function update_project_summary()
  local ok, payload = api_request("GET", "/state/project", nil)
  if not ok then
    state.project_summary = "State refresh failed: " .. tostring(type(payload) == "table" and payload.detail or payload)
    state.project_detail_lines = {}
    return
  end
  summarize_project(payload.project or {})
end

local function preview_prompt()
  if state.instance_read_only then
    state.last_result = "This panel instance is read-only because another panel is active."
    return
  end
  if state.prompt == "" then
    state.last_result = "Enter a prompt first."
    return
  end
  append_log("preview_click instance=" .. PANEL_INSTANCE_ID .. " prompt=" .. tostring(state.prompt))
  local request_payload = {
    prompt = state.prompt,
  }
  if next(state.clarification_answers) ~= nil then
    request_payload.clarification_answers = state.clarification_answers
  end
  local ok, payload = api_request(
    "POST",
    "/plan",
    request_payload
  )
  if not ok then
    local message = type(payload) == "table" and payload.detail or payload
    state.last_result = "Preview failed: " .. tostring(message)
    append_log("preview_failed instance=" .. PANEL_INSTANCE_ID .. " detail=" .. tostring(message))
    return
  end
  if payload.requires_clarification and payload.clarification then
    state.plan_id = nil
    state.plan_summary = payload.summary or "Clarification needed."
    state.plan_steps = {}
    state.clarification_id = payload.clarification.id
    state.clarification_question = payload.clarification.question
    state.clarification_options = payload.clarification.options or {}
    state.last_result = "Clarification needed before preview can continue."
    append_log("preview_clarification instance=" .. PANEL_INSTANCE_ID .. " clarification_id=" .. tostring(state.clarification_id))
    return
  end
  clear_clarification()
  state.plan_id = payload.plan_id
  state.plan_summary = payload.summary or "No summary."
  state.plan_steps = payload.steps or {}
  state.last_result = payload.ok and "Preview ready." or "Preview returned no executable steps."
  append_log("preview_ready instance=" .. PANEL_INSTANCE_ID .. " plan_id=" .. tostring(state.plan_id))
end

local function answer_clarification(value)
  if not state.clarification_id then
    return
  end
  state.clarification_answers[state.clarification_id] = tostring(value)
  state.last_result = "Clarification answered. Refreshing preview..."
  preview_prompt()
end

local function apply_plan()
  if state.instance_read_only then
    state.last_result = "This panel instance is read-only because another panel is active."
    return
  end
  if state.apply_pending then
    state.last_result = "Apply already in progress."
    return
  end
  if not state.plan_id then
    if state.clarification_id then
      state.last_result = "Answer the clarification first."
    else
      state.last_result = "Preview a plan first."
    end
    append_log("apply_blocked_missing_plan instance=" .. PANEL_INSTANCE_ID)
    return
  end
  append_log("apply_click instance=" .. PANEL_INSTANCE_ID .. " plan_id=" .. tostring(state.plan_id))
  if not start_apply_request(state.plan_id) then
    state.last_result = "Apply failed: could not launch background request."
    append_log("apply_launch_failed instance=" .. PANEL_INSTANCE_ID)
    return
  end
  state.apply_pending = true
  state.last_result = "Apply started. Waiting for REAPER bridge..."
  state.apply_summary = "Apply in progress."
  state.apply_result_lines = {"Waiting for bridge execution results..."}
  state.verification_summary = "Verification pending."
  state.verification_result_lines = {"Waiting for refreshed project state..."}
  state.mismatch_lines = {}
end

local function prompt_for_input()
  local ok, value = reaper.GetUserInputs("ReaperGPT Prompt", 1, "Prompt:", state.prompt)
  if ok then
    state.prompt = value
    state.clarification_answers = {}
    clear_clarification()
  end
end

local function point_in_rect(x, y, w, h, px, py)
  return px >= x and px <= (x + w) and py >= y and py <= (y + h)
end

local function draw_button(x, y, w, h, label)
  local hovered = point_in_rect(x, y, w, h, gfx.mouse_x, gfx.mouse_y)
  gfx.set(hovered and 0.30 or 0.18, hovered and 0.55 or 0.40, 0.78, 1)
  gfx.rect(x, y, w, h, true)
  gfx.set(1, 1, 1, 1)
  gfx.x = x + 10
  gfx.y = y + 8
  gfx.drawstr(label)
  local clicked = hovered and gfx.mouse_cap & 1 == 1 and not mouse.down
  return clicked
end

local function draw_section_title(text, y)
  gfx.set(0.85, 0.85, 0.85, 1)
  gfx.x = 16
  gfx.y = y
  gfx.drawstr(text)
end

local function draw_wrapped_text(text, x, y, width, line_height, max_lines)
  local remaining = tostring(text or "")
  local line = 0
  while remaining ~= "" and line < max_lines do
    local chunk = remaining
    gfx.setfont(1, "Arial", 16)
    while gfx.measurestr(chunk) > width do
      local cut = chunk:match("^.*()%s+[^%s]*$")
      if not cut then
        chunk = chunk:sub(1, math.max(1, math.floor(#chunk * 0.8)))
      else
        chunk = chunk:sub(1, cut - 1)
      end
    end
    gfx.x = x
    gfx.y = y + (line * line_height)
    gfx.drawstr(chunk)
    remaining = remaining:sub(#chunk + 1):gsub("^%s+", "")
    line = line + 1
  end
end

append_line = function(lines, text)
  if text == nil then
    return
  end
  lines[#lines + 1] = tostring(text)
end

local function bool_label(value)
  if value then
    return "yes"
  end
  return "no"
end

local function track_ref_label(ref)
  if type(ref) ~= "table" then
    return "?"
  end
  if ref.value ~= nil then
    return tostring(ref.value)
  end
  return "?"
end

summarize_project = function(project)
  local track_count = #(project.tracks or {})
  local send_count = #(project.sends or {})
  local selected_count = #(project.selected_track_ids or {})
  local marker_count = #(project.markers or {})
  local region_count = #(project.regions or {})
  state.project_summary = string.format(
    "%s | Tempo %s | Tracks %d | Sends %d | Selected %d",
    tostring(project.name or "Unknown project"),
    tostring(project.tempo or "?"),
    track_count,
    send_count,
    selected_count
  )

  local detail_lines = {}
  append_line(detail_lines, string.format("Markers %d | Regions %d | Bridge %s", marker_count, region_count, bool_label(project.bridge_connected)))

  local selected_names = {}
  for _, track in ipairs(project.selection and project.selection.tracks or {}) do
    selected_names[#selected_names + 1] = tostring(track.name or track.id or "?")
  end
  if #selected_names > 0 then
    append_line(detail_lines, "Selected tracks: " .. table.concat(selected_names, ", "))
  end

  local folder_names = {}
  for _, track in ipairs(project.folder_structure or {}) do
    if track.is_folder_parent then
      folder_names[#folder_names + 1] = tostring(track.name or track.id or "?")
    end
  end
  if #folder_names > 0 then
    append_line(detail_lines, "Folders: " .. table.concat(folder_names, ", "))
  end

  local bus_names = {}
  for _, track in ipairs(project.tracks or {}) do
    if track.is_bus then
      bus_names[#bus_names + 1] = tostring(track.name or track.id or "?")
    end
  end
  if #bus_names > 0 then
    append_line(detail_lines, "Buses: " .. table.concat(bus_names, ", "))
  end

  state.project_detail_lines = detail_lines
end

format_step_result_line = function(result)
  local prefix = string.format("%d. [%s] %s", tonumber(result.index or 0) + 1, tostring(result.status or "?"), tostring(result.tool or "unknown"))
  local detail = result.detail
  if type(detail) ~= "table" then
    if detail ~= nil then
      return prefix .. " - " .. tostring(detail)
    end
    return prefix
  end
  if result.tool == "create_track" or result.tool == "create_bus" then
    return prefix .. " - track_id " .. tostring(detail.track_id or "?")
  end
  if result.tool == "create_send" then
    return prefix .. " - send_index " .. tostring(detail.send_index or "?")
  end
  if result.tool == "insert_fx" then
    return prefix .. " - " .. tostring(detail.fx_name or "?") .. " @ fx_index " .. tostring(detail.fx_index or "?")
  end
  if result.tool == "project.set_tempo" then
    return prefix .. " - tempo " .. tostring(detail.tempo or "?")
  end
  return prefix
end

format_verification_line = function(item)
  local status = item.ok and "PASS" or "FAIL"
  local line = string.format("%d. [%s] %s", tonumber(item.index or 0) + 1, status, tostring(item.check or "check"))
  local expected = item.expected
  if type(expected) == "table" then
    if item.check == "track_created" or item.check == "bus_created" then
      line = line .. " - " .. tostring(expected.name or expected.track_id or "?")
    elseif item.check == "send_exists" then
      line = line .. " - " .. track_ref_label(expected.src) .. " -> " .. track_ref_label(expected.dst)
    elseif item.check == "fx_inserted" then
      line = line .. " - " .. tostring(expected.fx_name or "?") .. " on " .. track_ref_label(expected.track_ref)
    elseif item.check == "tempo_changed" then
      line = line .. " - " .. tostring(expected.bpm or "?") .. " BPM"
    end
  end
  if not item.ok and item.message then
    line = line .. " - " .. tostring(item.message)
  end
  return line
end

draw_text_lines = function(lines, x, y, width, line_height, max_lines)
  local line_y = y
  local rendered = 0
  for _, line in ipairs(lines or {}) do
    if rendered >= max_lines then
      break
    end
    draw_wrapped_text(line, x, line_y, width, line_height, 2)
    line_y = line_y + (line_height * 2)
    rendered = rendered + 1
  end
end

local function render()
  gfx.set(0.08, 0.09, 0.11, 1)
  gfx.rect(0, 0, gfx.w, gfx.h, true)
  gfx.setfont(1, "Arial", 16)

  if draw_button(16, 16, 110, 32, "Prompt...") then
    if state.instance_read_only then
      state.last_result = "This panel instance is read-only because another panel is active."
    else
      prompt_for_input()
    end
  end
  if draw_button(136, 16, 110, 32, "Preview") then
    preview_prompt()
  end
  if draw_button(256, 16, 110, 32, "Apply") then
    apply_plan()
  end
  if draw_button(376, 16, 110, 32, "Refresh") then
    if state.instance_read_only then
      state.last_result = "This panel instance is read-only because another panel is active."
    else
      update_project_summary()
    end
  end

  draw_section_title("Prompt", 64)
  gfx.set(1, 1, 1, 1)
  draw_wrapped_text(state.prompt ~= "" and state.prompt or "No prompt set.", 16, 88, gfx.w - 32, 18, 3)
  gfx.x = 16
  gfx.y = 128
  gfx.drawstr("Instance: " .. PANEL_INSTANCE_ID)
  if state.instance_warning then
    gfx.set(1, 0.75, 0.30, 1)
    draw_wrapped_text(state.instance_warning, 250, 128, gfx.w - 266, 18, 2)
    gfx.set(1, 1, 1, 1)
  end

  draw_section_title("Preview", 170)
  gfx.set(1, 1, 1, 1)
  draw_wrapped_text(state.plan_summary, 16, 194, gfx.w - 32, 18, 3)
  gfx.x = 16
  gfx.y = 250
  gfx.drawstr("Plan ID: " .. tostring(state.plan_id or "none"))
  if state.clarification_id and state.clarification_question then
    draw_wrapped_text(state.clarification_question, 16, 274, gfx.w - 32, 18, 3)
    for index = 1, math.min(#state.clarification_options, 4) do
      local option = state.clarification_options[index] or {}
      local label = tostring(option.label or option.value or ("Option " .. tostring(index)))
      if draw_button(16, 334 + ((index - 1) * 40), gfx.w - 32, 32, label) then
        answer_clarification(option.value or label)
      end
    end
  else
    for index = 1, math.min(#state.plan_steps, 8) do
      gfx.x = 16
      gfx.y = 274 + ((index - 1) * 18)
      gfx.drawstr(string.format("%d. %s", index, format_step(state.plan_steps[index])))
    end
  end

  draw_section_title("Apply Results", 430)
  gfx.set(1, 1, 1, 1)
  draw_wrapped_text(state.apply_summary, 16, 454, gfx.w - 32, 18, 2)
  draw_text_lines(state.apply_result_lines, 16, 492, gfx.w - 32, 18, 4)

  draw_section_title("Verification", 646)
  gfx.set(1, 1, 1, 1)
  draw_wrapped_text(state.verification_summary, 16, 670, gfx.w - 32, 18, 2)
  draw_text_lines(state.verification_result_lines, 16, 708, gfx.w - 32, 18, 4)

  draw_section_title("Mismatches", 862)
  gfx.set(1, 1, 1, 1)
  if #state.mismatch_lines > 0 then
    draw_text_lines(state.mismatch_lines, 16, 886, gfx.w - 32, 18, 3)
  else
    draw_wrapped_text("No mismatches reported.", 16, 886, gfx.w - 32, 18, 2)
  end

  draw_section_title("Final Project", 1004)
  gfx.set(1, 1, 1, 1)
  draw_wrapped_text(state.project_summary, 16, 1028, gfx.w - 32, 18, 2)
  draw_text_lines(state.project_detail_lines, 16, 1066, gfx.w - 32, 18, 4)

  draw_section_title("Status", 1210)
  gfx.set(1, 1, 1, 1)
  draw_wrapped_text(state.last_result, 16, 1234, gfx.w - 32, 18, 3)
end

local function main()
  local char = gfx.getchar()
  if char < 0 then
    clear_active_instance()
    return
  end
  poll_apply_result()
  heartbeat_active_instance()
  render()
  mouse.down = gfx.mouse_cap & 1 == 1
  gfx.update()
  reaper.defer(main)
end

reaper.RecursiveCreateDirectory(DATA_DIR, 0)
write_active_instance()
gfx.init("ReaperGPT Panel", 640, 1320)
update_project_summary()
main()
