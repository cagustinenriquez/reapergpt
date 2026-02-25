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


def _heuristic_actions_for_prompt(prompt: str) -> list[ReaperAction]:
    cleaned = prompt.strip()
    lower = _normalize_prompt(cleaned)
    actions: list[ReaperAction] = []

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

    track_index = _extract_track_index(cleaned)
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


def _ollama_plan(prompt: str, settings: Settings) -> ActionBatch | None:
    system_prompt = (
        "You convert music production instructions into a strict JSON object with shape "
        '{"actions":[{"type":"...", "params":{}}]}. '
        "Allowed action types: transport.play, transport.stop, project.set_tempo, "
        "regions.create_song_form, track.select, track.mute, track.solo, track.record_arm. "
        "For regions.create_song_form in this MVP, params must be {}. "
        "track.select params: {\"track_index\": <int>=1+}. "
        "track.mute / track.solo / track.record_arm params: "
        "{\"track_index\": <int>=1+, \"enabled\": <bool>}. "
        "Return JSON only, no prose."
    )
    user_prompt = (
        "Instruction: "
        + prompt
        + "\nIf unsupported, return {\"actions\":[]}."
    )
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


def plan_prompt_to_actions(prompt: str, settings: Settings) -> PlanningResult:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must not be empty")

    provider = settings.llm_provider.strip().lower()
    if provider == "ollama":
        try:
            batch = _ollama_plan(prompt, settings)
            if batch and batch.actions:
                return PlanningResult(batch=batch, source="ollama")
        except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError):
            pass

    heuristic_actions = _heuristic_actions_for_prompt(prompt)
    if heuristic_actions:
        return PlanningResult(batch=ActionBatch(actions=heuristic_actions), source="heuristic")

    return PlanningResult(batch=None, source="unsupported")
