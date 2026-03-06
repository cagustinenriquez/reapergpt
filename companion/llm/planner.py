from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from companion.config import Settings
from companion.models.actions import ActionBatch, ReaperAction


@dataclass(frozen=True)
class PlanningResult:
    batch: ActionBatch | None
    source: str
    llm_error: str | None = None


def _extract_json_object(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            parsed, end = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        if end <= 0:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _normalize_prompt(prompt: str) -> str:
    text = re.sub(r"[^\w\s.:-]", " ", prompt.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_tempo_bpm(prompt: str) -> float | None:
    lower = _normalize_prompt(prompt)
    patterns = [
        r"\b(?:tempo|bpm)\s*(?:to\s+)?(\d+(?:\.\d+)?)\b",
        r"\bset\s+(?:the\s+)?(?:tempo|bpm)\s*(?:to\s+)?(\d+(?:\.\d+)?)\b",
        r"\b(\d+(?:\.\d+)?)\s*bpm\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lower)
        if not match:
            continue
        bpm = float(match.group(1))
        if bpm > 0:
            return bpm
    return None


def _contains_any_phrase(text: str, phrases: set[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _extract_track_index(prompt: str) -> int | None:
    lower = _normalize_prompt(prompt)
    match = re.search(r"\btrack\s+(\d+)\b", lower)
    if not match:
        return None
    track_index = int(match.group(1))
    return track_index if track_index > 0 else None


def _extract_track_delete_request(prompt: str) -> int | None:
    text = prompt.strip()
    patterns = [
        r"\bdelete\s+track\s+(\d+)\b",
        r"\bremove\s+track\s+(\d+)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        idx = int(m.group(1))
        if idx > 0:
            return idx
    return None


def _extract_track_volume_db(prompt: str) -> tuple[int, float] | None:
    lower = _normalize_prompt(prompt)
    patterns = [
        r"\bset\s+volume\s+(?:to\s+)?(-?\d+(?:\.\d+)?)\s*d\s*b\b.*\btrack\s+(\d+)\b",
        r"\bset\s+volume\s+(?:to\s+)?(-?\d+(?:\.\d+)?)\s*db\b.*\btrack\s+(\d+)\b",
        r"\btrack\s+(\d+)\b.*\bvolume\s+(?:to\s+)?(-?\d+(?:\.\d+)?)\s*d\s*b\b",
        r"\btrack\s+(\d+)\b.*\bvolume\s+(?:to\s+)?(-?\d+(?:\.\d+)?)\s*db\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, lower)
        if not m:
            continue
        if pattern.startswith(r"\btrack"):
            track_index = int(m.group(1))
            db = float(m.group(2))
        else:
            db = float(m.group(1))
            track_index = int(m.group(2))
        if track_index > 0:
            return track_index, db
    return None


def _extract_track_pan(prompt: str) -> tuple[int, float] | None:
    text = prompt.strip()
    center_patterns = [
        r"\bset\s+pan\s+(?:to\s+)?center\b.*\btrack\s+(\d+)\b",
        r"\btrack\s+(\d+)\b.*\bpan\s+(?:to\s+)?center\b",
    ]
    for pattern in center_patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            return int(m.group(1)), 0.0

    patterns = [
        r"\bset\s+pan\s+(?:to\s+)?(-?\d+(?:\.\d+)?)\s*%?\s*(left|right)\b.*\btrack\s+(\d+)\b",
        r"\btrack\s+(\d+)\b.*\bpan\s+(?:to\s+)?(-?\d+(?:\.\d+)?)\s*%?\s*(left|right)\b",
        r"\bpan\s+track\s+(\d+)\s+(left|right)\s*(-?\d+(?:\.\d+)?)\s*%?\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        if pattern.startswith(r"\bset"):
            amount = float(m.group(1))
            direction = m.group(2)
            track_index = int(m.group(3))
        elif pattern.startswith(r"\btrack"):
            track_index = int(m.group(1))
            amount = float(m.group(2))
            direction = m.group(3)
        else:
            track_index = int(m.group(1))
            direction = m.group(2)
            amount = float(m.group(3))
        amount = abs(amount) / 100.0
        pan = min(max(amount, 0.0), 1.0)
        if direction == "left":
            pan = -pan
        if track_index > 0:
            return track_index, pan
    return None


def _extract_track_rename(prompt: str) -> tuple[int, str] | None:
    text = prompt.strip()
    patterns = [
        r"\brename\s+track\s+(\d+)\s+to\s+(.+)$",
        r"\bset\s+track\s+(\d+)\s+name\s+to\s+(.+)$",
        r"\bname\s+track\s+(\d+)\s+(.+)$",
        r"\btrack\s+(\d+)\s+name\s+(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        track_index = int(m.group(1))
        raw_name = (m.group(2) or "").strip()
        raw_name = raw_name.strip('"').strip("'").strip()
        if track_index > 0 and raw_name:
            return track_index, raw_name
    return None


def _extract_track_create_name(prompt: str) -> str | None:
    text = prompt.strip()
    patterns = [
        r"\b(?:create|add|make)\s+(?:a\s+new\s+|new\s+)?track\s+(?:named|called)\s+(.+)$",
        r"\b(?:create|add|make)\s+(?:a\s+new\s+|new\s+)?track\s+name\s+(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        raw_name = (m.group(1) or "").strip()
        raw_name = raw_name.strip('"').strip("'").strip()
        if raw_name:
            return raw_name
    return None


def _extract_reaper_action_request(prompt: str) -> dict[str, Any] | None:
    text = prompt.strip()
    lower = _normalize_prompt(text)

    m = re.search(r"\b(?:run|execute|trigger)\s+(?:reaper\s+)?action\s+(\d+)\b", lower)
    if m:
        return {"command_id": int(m.group(1))}

    m = re.search(r"\b(?:run|execute|trigger)\s+(?:reaper\s+)?command\s+([_A-Za-z0-9]+)\b", text)
    if m:
        return {"command_name": m.group(1)}

    return None


def _parse_time_token_to_seconds(token: str) -> float | None:
    t = (token or "").strip()
    if not t:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        return float(t)
    m = re.fullmatch(r"(\d+):(\d{1,2})(?:\.(\d+))?", t)
    if m:
        minutes = int(m.group(1))
        seconds = int(m.group(2))
        frac = float(f"0.{m.group(3)}") if m.group(3) else 0.0
        return (minutes * 60.0) + seconds + frac
    h = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?", t)
    if h:
        hours = int(h.group(1))
        minutes = int(h.group(2))
        seconds = int(h.group(3))
        frac = float(f"0.{h.group(4)}") if h.group(4) else 0.0
        return (hours * 3600.0) + (minutes * 60.0) + seconds + frac
    return None


def _extract_track_input_request(prompt: str, allow_last_created: bool = False) -> dict[str, Any] | None:
    text = prompt.strip()

    midi = re.search(
        r"\b(?:set\s+)?(?:the\s+)?input\s+of\s+track\s+(\d+)\s+to\s+midi\s*#?\s*(\d+)\b",
        text,
        re.IGNORECASE,
    )
    if midi:
        return {
            "track_index": int(midi.group(1)),
            "input_type": "midi",
            "input_index": int(midi.group(2)),
            "midi_channel": 0,
        }

    midi2 = re.search(
        r"\bmidi\s*#?\s*(\d+)\s+input\s+on\s+track\s+(\d+)\b",
        text,
        re.IGNORECASE,
    )
    if midi2:
        return {
            "track_index": int(midi2.group(2)),
            "input_type": "midi",
            "input_index": int(midi2.group(1)),
            "midi_channel": 0,
        }

    audio = re.search(
        r"\b(?:set\s+)?(?:the\s+)?input\s+of\s+track\s+(\d+)\s+to\s+(stereo\s+)?input\s*#?\s*(\d+)\b",
        text,
        re.IGNORECASE,
    )
    if audio:
        return {
            "track_index": int(audio.group(1)),
            "input_type": "audio",
            "input_index": int(audio.group(3)),
            "stereo": bool(audio.group(2)),
        }

    audio2 = re.search(
        r"\binput\s*#?\s*(\d+)\s+on\s+track\s+(\d+)\b",
        text,
        re.IGNORECASE,
    )
    if audio2:
        return {
            "track_index": int(audio2.group(2)),
            "input_type": "audio",
            "input_index": int(audio2.group(1)),
            "stereo": False,
        }

    if allow_last_created:
        lower = text.lower()
        general = re.search(
            r"\bset\s+(?:the\s+)?input\s+(?:to|into)\s+(midi|audio)\b",
            text,
            re.IGNORECASE,
        )
        if general:
            input_type = general.group(1).lower()
            params: dict[str, Any] = {
                "track_ref": "last_created",
                "input_type": "midi" if input_type == "midi" else "audio",
                "input_index": 1,
            }
            if params["input_type"] == "midi":
                params["midi_channel"] = 0
            return params

    return None


def _extract_track_stereo_request(prompt: str) -> tuple[int, bool] | None:
    text = prompt.strip()
    m = re.search(r"\bmake\s+track\s+(\d+)\s+stereo\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), True
    m = re.search(r"\bmake\s+track\s+(\d+)\s+mono\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), False
    m = re.search(r"\bset\s+track\s+(\d+)\s+to\s+(stereo|mono)\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1)), m.group(2).lower() == "stereo"
    return None


def _extract_pan_phrase_value(text: str, key: str) -> float | None:
    m = re.search(rf"\b{key}\s+pan\s+(center|(?:\d+(?:\.\d+)?)\s*%?\s*(?:left|right))\b", text, re.IGNORECASE)
    if not m:
        return None
    phrase = m.group(1).strip().lower()
    if phrase == "center":
        return 0.0
    m2 = re.match(r"(\d+(?:\.\d+)?)\s*%?\s*(left|right)", phrase)
    if not m2:
        return None
    amount = min(max(float(m2.group(1)) / 100.0, 0.0), 1.0)
    return -amount if m2.group(2) == "left" else amount


def _extract_pan_ramp_request(prompt: str) -> dict[str, Any] | None:
    text = prompt.strip()
    m = re.search(
        r"\b(?:set\s+)?automation\s+for\s+pan\s+for\s+track\s+(\d+)\s+from\s+([0-9:.]+)\s+to\s+([0-9:.]+)\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"\bpan\s+automation\s+for\s+track\s+(\d+)\s+from\s+([0-9:.]+)\s+to\s+([0-9:.]+)\b",
            text,
            re.IGNORECASE,
        )
    if not m:
        return None
    start_t = _parse_time_token_to_seconds(m.group(2))
    end_t = _parse_time_token_to_seconds(m.group(3))
    if start_t is None or end_t is None:
        return None
    start_pan = _extract_pan_phrase_value(text, "start") or 0.0
    end_pan = _extract_pan_phrase_value(text, "end") or 0.0
    return {
        "track_index": int(m.group(1)),
        "start_time_seconds": start_t,
        "end_time_seconds": end_t,
        "start_pan": start_pan,
        "end_pan": end_pan,
    }


def _extract_volume_phrase_db(text: str, key: str) -> float | None:
    m = re.search(rf"\b{key}\s+volume\s+(-?\d+(?:\.\d+)?)\s*d\s*b\b", text, re.IGNORECASE)
    if not m:
        m = re.search(rf"\b{key}\s+volume\s+(-?\d+(?:\.\d+)?)\s*db\b", text, re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1))


def _extract_volume_ramp_request(prompt: str) -> dict[str, Any] | None:
    text = prompt.strip()
    m = re.search(
        r"\b(?:set\s+)?automation\s+for\s+volume\s+for\s+track\s+(\d+)\s+from\s+([0-9:.]+)\s+to\s+([0-9:.]+)\b",
        text,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"\bvolume\s+automation\s+for\s+track\s+(\d+)\s+from\s+([0-9:.]+)\s+to\s+([0-9:.]+)\b",
            text,
            re.IGNORECASE,
        )
    if not m:
        return None
    start_t = _parse_time_token_to_seconds(m.group(2))
    end_t = _parse_time_token_to_seconds(m.group(3))
    if start_t is None or end_t is None:
        return None
    start_db = _extract_volume_phrase_db(text, "start")
    end_db = _extract_volume_phrase_db(text, "end")
    if start_db is None:
        start_db = 0.0
    if end_db is None:
        end_db = 0.0
    return {
        "track_index": int(m.group(1)),
        "start_time_seconds": start_t,
        "end_time_seconds": end_t,
        "start_db": start_db,
        "end_db": end_db,
    }


def _extract_track_monitoring_request(prompt: str) -> tuple[int, bool] | None:
    text = prompt.strip()
    patterns = [
        (r"\b(?:enable|turn\s+on)\s+(?:input\s+)?monitoring\s+on\s+track\s+(\d+)\b", True),
        (r"\b(?:disable|turn\s+off)\s+(?:input\s+)?monitoring\s+on\s+track\s+(\d+)\b", False),
        (r"\btrack\s+(\d+)\s+(?:input\s+)?monitoring\s+(on|off)\b", None),
        (r"\bset\s+(?:input\s+)?monitoring\s+(on|off)\s+for\s+track\s+(\d+)\b", None),
    ]
    for pattern, fixed in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        if fixed is not None:
            return int(m.group(1)), fixed
        if pattern.startswith(r"\btrack"):
            return int(m.group(1)), m.group(2).lower() == "on"
        return int(m.group(2)), m.group(1).lower() == "on"
    return None


def _extract_track_record_mode_request(prompt: str) -> tuple[int, str] | None:
    text = prompt.strip()
    patterns = [
        r"\bset\s+record\s+mode\s+to\s+([a-z ]+)\s+on\s+track\s+(\d+)\b",
        r"\bset\s+track\s+(\d+)\s+record\s+mode\s+to\s+([a-z ]+)\b",
        r"\btrack\s+(\d+)\s+record\s+mode\s+([a-z ]+)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        if pattern.startswith(r"\bset\s+record"):
            mode_raw = m.group(1)
            track_index = int(m.group(2))
        else:
            track_index = int(m.group(1))
            mode_raw = m.group(2)
        mode = re.sub(r"\s+", " ", mode_raw.strip().lower())
        mode_aliases = {
            "input": "input",
            "normal": "input",
            "midi overdub": "midi_overdub",
            "overdub": "midi_overdub",
            "midi replace": "midi_replace",
            "replace": "midi_replace",
        }
        if track_index > 0 and mode in mode_aliases:
            return track_index, mode_aliases[mode]
    return None


def _extract_render_region_request(prompt: str) -> dict[str, Any] | None:
    text = prompt.strip()
    lower = _normalize_prompt(text)
    if "render" not in lower or "region" not in lower:
        return None
    if "selected region" not in lower:
        return None
    if "desktop" not in lower:
        return None
    if "mp3" not in lower:
        return None

    bitrate = None
    m = re.search(r"\b(\d{2,4})\s*k\s*b\s*p\s*s\b", lower)
    if not m:
        m = re.search(r"\b(\d{2,4})\s*kbps\b", lower)
    if m:
        bitrate = int(m.group(1))
    if bitrate is None:
        bitrate = 128

    return {
        "region_scope": "selected",
        "format": "mp3",
        "mp3_bitrate_kbps": bitrate,
        "output_dir": "desktop",
    }


def _extract_track_routing_request(prompt: str) -> tuple[str, dict[str, Any]] | None:
    text = prompt.strip()
    m = re.search(
        r"\bcreate\s+send\s+from\s+track\s+(\d+)\s+to\s+track\s+(\d+)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        return "track.create_send", {
            "source_track_index": int(m.group(1)),
            "dest_track_index": int(m.group(2)),
        }

    m = re.search(
        r"\bcreate\s+receive\s+from\s+track\s+(\d+)\s+to\s+(?:track\s+)?(\d+)\b",
        text,
        re.IGNORECASE,
    )
    if m:
        src = int(m.group(1))
        dst = int(m.group(2))
        # "create receive from track 7 to 8" means track 8 receives from track 7 => same route as send 7->8.
        return "track.create_receive", {
            "source_track_index": src,
            "dest_track_index": dst,
        }
    return None


def _extract_track_color(prompt: str) -> str | None:
    lower = _normalize_prompt(prompt)
    known = {"red", "orange", "yellow", "green", "blue", "purple", "pink", "white", "black"}
    for color in known:
        if re.search(rf"\b(?:color(?:ed)?|colour(?:ed)?)\s+(?:it\s+)?{color}\b", lower):
            return color
        if re.search(rf"\bcolor\s+track\s+\d+\s+{color}\b", lower):
            return color
        if re.search(rf"\btrack\s+\d+\s+(?:color|colored|colour|coloured)\s+{color}\b", lower):
            return color
        if re.search(rf"\b{color}\s+track\b", lower):
            return color
    return None


def _normalize_fx_name(raw_name: str) -> str:
    text = re.sub(r"\s+", " ", raw_name.strip())
    aliases = {
        "fabfilter q4": "FabFilter Pro-Q 4",
        "fabfilter pro q4": "FabFilter Pro-Q 4",
        "fabfilter pro-q 4": "FabFilter Pro-Q 4",
        "pro q4": "FabFilter Pro-Q 4",
        "pro-q 4": "FabFilter Pro-Q 4",
    }
    return aliases.get(text.lower(), text)


def _extract_fx_name(prompt: str) -> str | None:
    lower = _normalize_prompt(prompt)
    if " add " not in f" {lower} " and not lower.startswith("add "):
        return None
    if re.search(r"\badd\s+(?:a\s+new\s+|new\s+)?track\b", lower):
        return None

    match = re.search(r"\badd\s+(.+?)\s+to\s+(?:it|track\s+\d+)\b", lower)
    if match:
        return _normalize_fx_name(match.group(1))

    match = re.search(r"\badd\s+(.+?)\s*(?:$|\bplugin\b|\bfx\b)", lower)
    if match:
        candidate = match.group(1).strip()
        if candidate:
            return _normalize_fx_name(candidate)
    return None


def _heuristic_actions_for_prompt(prompt: str, preferences: dict[str, Any] | None = None) -> list[ReaperAction]:
    cleaned = prompt.strip()
    lower = _normalize_prompt(cleaned)
    actions: list[ReaperAction] = []
    preferences = preferences or {}

    if not cleaned:
        return actions

    wants_play = _contains_any_phrase(
        lower,
        {
            " play",
            "play ",
            "start playback",
            "start transport",
            "playback start",
            "resume playback",
        },
    ) or lower in {"play", "start", "start playback", "start transport"}

    wants_stop = _contains_any_phrase(
        lower,
        {
            " stop",
            "stop ",
            "stop playback",
            "stop transport",
            "pause playback",
            "pause transport",
        },
    ) or lower in {"stop", "pause", "pause playback", "stop playback"}

    bpm = _extract_tempo_bpm(cleaned)
    if bpm is not None:
        actions.append(ReaperAction(type="project.set_tempo", params={"bpm": bpm}))

    wants_track_create = any(
        phrase in lower
        for phrase in {
            "create track",
            "create a new track",
            "create new track",
            "add track",
            "add a new track",
            "add new track",
            "make a new track",
            "make new track",
        }
    )
    if lower in {"make track", "make a track"}:
        wants_track_create = True

    track_index = _extract_track_index(cleaned)
    volume_target = _extract_track_volume_db(cleaned)
    pan_target = _extract_track_pan(cleaned)
    rename_target = _extract_track_rename(cleaned)
    input_target = _extract_track_input_request(cleaned, allow_last_created=wants_track_create)
    stereo_target = _extract_track_stereo_request(cleaned)
    pan_ramp_target = _extract_pan_ramp_request(cleaned)
    volume_ramp_target = _extract_volume_ramp_request(cleaned)
    monitoring_target = _extract_track_monitoring_request(cleaned)
    record_mode_target = _extract_track_record_mode_request(cleaned)
    render_region_target = _extract_render_region_request(cleaned)
    routing_target = _extract_track_routing_request(cleaned)
    reaper_action_target = _extract_reaper_action_request(cleaned)
    track_color = _extract_track_color(cleaned)
    fx_name = _extract_fx_name(cleaned)
    track_create_name = _extract_track_create_name(cleaned)
    track_delete_index = _extract_track_delete_request(cleaned)

    if wants_track_create:
        create_params: dict[str, Any] = {}
        if track_create_name:
            create_params["name"] = track_create_name
        elif isinstance(preferences.get("track_naming_prefix"), str) and preferences.get("track_naming_prefix", "").strip():
            create_params["name"] = f"{preferences['track_naming_prefix'].strip()} 1"
        actions.append(ReaperAction(type="track.create", params=create_params))

    default_color = preferences.get("default_track_color")
    if (
        not track_color
        and wants_track_create
        and isinstance(default_color, str)
        and default_color.strip()
    ):
        track_color = default_color.strip().lower()

    if track_delete_index is not None:
        actions.append(ReaperAction(type="track.delete", params={"track_index": track_delete_index}))

    if track_color:
        color_params: dict[str, Any] = {"color": track_color}
        if wants_track_create and (" to it" in f" {lower} " or " it " in f" {lower} "):
            color_params["track_ref"] = "last_created"
        elif track_index is not None:
            color_params["track_index"] = track_index
        elif wants_track_create:
            color_params["track_ref"] = "last_created"
        else:
            color_params = {}
        if color_params:
            actions.append(ReaperAction(type="track.set_color", params=color_params))

    if fx_name:
        fx_params: dict[str, Any] = {"fx_name": fx_name}
        if wants_track_create and (" to it" in f" {lower} " or " it " in f" {lower} "):
            fx_params["track_ref"] = "last_created"
        elif track_index is not None:
            fx_params["track_index"] = track_index
        elif wants_track_create:
            fx_params["track_ref"] = "last_created"
        else:
            fx_params = {}
        if fx_params:
            actions.append(ReaperAction(type="fx.add", params=fx_params))

    if volume_target is not None:
        volume_track_index, volume_db = volume_target
        actions.append(
            ReaperAction(type="track.set_volume", params={"track_index": volume_track_index, "db": volume_db})
        )

    if pan_target is not None:
        pan_track_index, pan = pan_target
        actions.append(ReaperAction(type="track.set_pan", params={"track_index": pan_track_index, "pan": pan}))

    if rename_target is not None:
        name_track_index, track_name = rename_target
        actions.append(
            ReaperAction(type="track.set_name", params={"track_index": name_track_index, "name": track_name})
        )

    if input_target is not None:
        actions.append(ReaperAction(type="track.set_input", params=input_target))

    if stereo_target is not None:
        stereo_track_index, stereo_enabled = stereo_target
        actions.append(
            ReaperAction(type="track.set_stereo", params={"track_index": stereo_track_index, "enabled": stereo_enabled})
        )

    if pan_ramp_target is not None:
        actions.append(ReaperAction(type="automation.pan_ramp", params=pan_ramp_target))

    if volume_ramp_target is not None:
        actions.append(ReaperAction(type="automation.volume_ramp", params=volume_ramp_target))

    if monitoring_target is not None:
        mon_track_index, mon_enabled = monitoring_target
        actions.append(
            ReaperAction(type="track.set_monitoring", params={"track_index": mon_track_index, "enabled": mon_enabled})
        )

    if record_mode_target is not None:
        rec_track_index, rec_mode = record_mode_target
        actions.append(
            ReaperAction(type="track.set_record_mode", params={"track_index": rec_track_index, "mode": rec_mode})
        )

    if render_region_target is not None:
        actions.append(ReaperAction(type="project.render_region", params=render_region_target))

    if routing_target is not None:
        routing_action_type, routing_params = routing_target
        actions.append(ReaperAction(type=routing_action_type, params=routing_params))

    if reaper_action_target is not None:
        actions.append(ReaperAction(type="reaper.action", params=reaper_action_target))

    if track_index is not None:
        if "select track" in lower:
            actions.append(ReaperAction(type="track.select", params={"track_index": track_index}))

        if "unmute" in lower:
            actions.append(
                ReaperAction(
                    type="track.mute",
                    params={"track_index": track_index, "enabled": False},
                )
            )
        elif "mute" in lower:
            actions.append(
                ReaperAction(
                    type="track.mute",
                    params={"track_index": track_index, "enabled": True},
                )
            )

        if "unsolo" in lower:
            actions.append(
                ReaperAction(
                    type="track.solo",
                    params={"track_index": track_index, "enabled": False},
                )
            )
        elif "solo" in lower:
            actions.append(
                ReaperAction(
                    type="track.solo",
                    params={"track_index": track_index, "enabled": True},
                )
            )

        if "disarm" in lower or "unarm" in lower:
            actions.append(
                ReaperAction(
                    type="track.record_arm",
                    params={"track_index": track_index, "enabled": False},
                )
            )
        elif "record arm" in lower or "arm track" in lower:
            actions.append(
                ReaperAction(
                    type="track.record_arm",
                    params={"track_index": track_index, "enabled": True},
                )
            )

    wants_regions = "region" in lower or "regions" in lower
    wants_structure = any(word in lower for word in {"song form", "song structure", "arrangement"})
    wants_pop_form = "pop" in lower and (wants_regions or wants_structure)
    wants_rock_form = "rock" in lower and (wants_regions or wants_structure)

    if wants_pop_form:
        actions.append(ReaperAction(type="regions.create_song_form", params={}))

    if wants_rock_form and not wants_pop_form:
        # MVP maps rock to same executor action until style params are added.
        actions.append(ReaperAction(type="regions.create_song_form", params={}))

    if wants_play and not wants_stop:
        actions.append(ReaperAction(type="transport.play", params={}))
    elif wants_stop and not wants_play:
        actions.append(ReaperAction(type="transport.stop", params={}))
    elif wants_play and wants_stop:
        play_pos = lower.find("play")
        stop_pos = lower.find("stop")
        pause_pos = lower.find("pause")
        effective_stop_pos = min([p for p in [stop_pos, pause_pos] if p != -1], default=-1)
        if effective_stop_pos == -1 or (play_pos != -1 and play_pos < effective_stop_pos):
            actions.append(ReaperAction(type="transport.play", params={}))
            actions.append(ReaperAction(type="transport.stop", params={}))
        else:
            actions.append(ReaperAction(type="transport.stop", params={}))
            actions.append(ReaperAction(type="transport.play", params={}))

    # Deduplicate repeated heuristic hits while preserving order.
    has_volume_ramp = any(a.type.value == "automation.volume_ramp" for a in actions)
    if has_volume_ramp:
        actions = [a for a in actions if a.type.value != "track.set_volume"]

    deduped: list[ReaperAction] = []
    seen: set[tuple[str, str]] = set()
    for action in actions:
        key = (action.type.value, json.dumps(action.params, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)

    return deduped


def _action_batch_from_obj(obj: dict[str, Any]) -> ActionBatch | None:
    raw_actions = obj.get("actions")
    if raw_actions is None and isinstance(obj.get("batch"), dict):
        raw_actions = obj["batch"].get("actions")
    if not isinstance(raw_actions, list) or not raw_actions:
        return None

    sanitized_actions: list[dict[str, Any]] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        if "type" not in item:
            continue
        sanitized_actions.append(
            {
                "type": item["type"],
                "params": item.get("params", {}) or {},
            }
        )

    if not sanitized_actions:
        return None
    return ActionBatch(actions=[ReaperAction(**item) for item in sanitized_actions])


def _ollama_plan(
    prompt: str,
    settings: Settings,
    project_state: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
) -> ActionBatch | None:
    system_prompt = (
        "You convert music production instructions into a strict JSON object with shape "
        '{"actions":[{"type":"...", "params":{}}]}. '
        "Allowed action types: transport.play, transport.stop, project.set_tempo, "
        "project.render_region, regions.create_song_form, track.create, track.delete, track.select, track.set_color, "
        "track.set_volume, track.set_pan, track.set_name, track.set_input, track.set_stereo, "
        "track.set_monitoring, track.set_record_mode, track.mute, track.solo, track.record_arm, "
        "track.create_send, track.create_receive, fx.add, automation.pan_ramp, automation.volume_ramp, reaper.action. "
        "For regions.create_song_form in this MVP, params must be {}. "
        "project.render_region params: {\"region_scope\":\"selected\",\"format\":\"mp3\",\"mp3_bitrate_kbps\":<int>,\"output_dir\":\"desktop\"}. "
        "track.create params: {} or {\"name\": <string>}. "
        "track.delete params: {\"track_index\": <int>=1+}. "
        "track.select params: {\"track_index\": <int>=1+}. "
        "track.set_color params: {\"color\": <string>, \"track_index\": <int>=1+} "
        "or {\"color\": <string>, \"track_ref\": \"last_created\"}. "
        "track.set_volume params: {\"track_index\": <int>=1+, \"db\": <number>}. "
        "track.set_pan params: {\"track_index\": <int>=1+, \"pan\": <-1..1>}. "
        "track.set_name params: {\"track_index\": <int>=1+, \"name\": <string>}. "
        "track.set_input params: {\"track_index\": <int>=1+, \"input_type\": \"audio\"|\"midi\", "
        "\"input_index\": <int>=1+, optional \"stereo\": <bool>, optional \"midi_channel\": <0..16>}. "
        "track.set_stereo params: {\"track_index\": <int>=1+, \"enabled\": <bool>}. "
        "track.set_monitoring params: {\"track_index\": <int>=1+, \"enabled\": <bool>}. "
        "track.set_record_mode params: {\"track_index\": <int>=1+, \"mode\": \"input\"|\"midi_overdub\"|\"midi_replace\"}. "
        "track.create_send / track.create_receive params: {\"source_track_index\": <int>=1+, \"dest_track_index\": <int>=1+}. "
        "track.mute / track.solo / track.record_arm params: "
        "{\"track_index\": <int>=1+, \"enabled\": <bool>}. "
        "fx.add params: {\"fx_name\": <string>, \"track_index\": <int>=1+} "
        "or {\"fx_name\": <string>, \"track_ref\": \"last_created\"}. "
        "automation.pan_ramp params: {\"track_index\": <int>=1+, \"start_time_seconds\": <number>, "
        "\"end_time_seconds\": <number>, \"start_pan\": <-1..1>, \"end_pan\": <-1..1>}. "
        "automation.volume_ramp params: {\"track_index\": <int>=1+, \"start_time_seconds\": <number>, "
        "\"end_time_seconds\": <number>, \"start_db\": <number>, \"end_db\": <number>}. "
        "reaper.action params: {\"command_id\": <int>} or {\"command_name\": <string>} "
        "with optional {\"section_id\": <int>} for advanced use. "
        "Return JSON only, no prose."
    )
    context_json = ""
    if isinstance(project_state, dict):
        try:
            # Keep context bounded for small local models while still being useful.
            raw = json.dumps(project_state, separators=(",", ":"), ensure_ascii=True)
            context_json = raw[:12000]
        except (TypeError, ValueError):
            context_json = ""
    user_prompt = "Instruction: " + prompt
    if context_json:
        user_prompt += (
            "\nProject snapshot (current REAPER state, use when relevant for track/routing decisions): "
            + context_json
        )
    if isinstance(preferences, dict) and preferences:
        try:
            pref_json = json.dumps(preferences, separators=(",", ":"), ensure_ascii=True)[:4000]
            user_prompt += (
                "\nUser profile preferences (apply when the instruction implies defaults such as "
                "'my style' or unspecified track color/name): " + pref_json
            )
        except (TypeError, ValueError):
            pass
    user_prompt += "\nIf unsupported, return {\"actions\":[]}."
    payload = {
        "model": settings.ollama_model,
        "prompt": f"System: {system_prompt}\nUser: {user_prompt}\nAssistant:",
        "stream": False,
        "format": "json",
    }

    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        response = client.post(f"{settings.ollama_base_url.rstrip('/')}/api/generate", json=payload)
        response.raise_for_status()
        data = response.json()

    obj = _extract_json_object(str(data.get("response", "")))
    if not obj:
        return None
    return _action_batch_from_obj(obj)


def plan_prompt_to_actions(
    prompt: str,
    settings: Settings,
    project_state: dict[str, Any] | None = None,
    preferences: dict[str, Any] | None = None,
    allow_heuristic_fallback: bool = True,
) -> PlanningResult:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must not be empty")

    provider = settings.llm_provider.strip().lower()
    llm_error: str | None = None
    if provider == "ollama":
        try:
            batch = _ollama_plan(prompt, settings, project_state=project_state, preferences=preferences)
            if batch and batch.actions:
                return PlanningResult(batch=batch, source="ollama")
            if not allow_heuristic_fallback:
                return PlanningResult(
                    batch=None,
                    source="unsupported",
                    llm_error="Ollama returned no supported actions for this prompt",
                )
        except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError) as exc:
            llm_error = f"{type(exc).__name__}: {exc}"
            if not allow_heuristic_fallback:
                return PlanningResult(batch=None, source="ollama_error", llm_error=llm_error)

    heuristic_actions = _heuristic_actions_for_prompt(prompt, preferences=preferences)
    if heuristic_actions:
        return PlanningResult(batch=ActionBatch(actions=heuristic_actions), source="heuristic", llm_error=llm_error)

    return PlanningResult(batch=None, source="unsupported", llm_error=llm_error)
