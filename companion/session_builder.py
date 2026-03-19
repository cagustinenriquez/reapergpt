from __future__ import annotations

import re

from companion.models.schemas import BridgePlanStep
from companion.models.session_builder_plan import (
    BusCreateAction,
    EntityRef,
    PromptRequest,
    SessionBuilderPlan,
    SendCreateAction,
    TrackCreateAction,
)


def build_mock_prompt_plan(payload: PromptRequest) -> SessionBuilderPlan:
    prompt = payload.prompt.strip()
    if not prompt:
        raise ValueError("prompt must not be empty")

    tracks, buses = _extract_named_creations(prompt)
    explicit_routes = _extract_route_pairs(prompt)
    should_route = bool(explicit_routes) or _wants_routing(prompt)

    if should_route and not buses:
        inferred_bus = _infer_bus_name(tracks)
        if inferred_bus:
            buses.append(inferred_bus)

    if not tracks and not buses and not explicit_routes:
        raise ValueError("no supported Session Builder actions found in prompt")

    actions = []
    steps = []
    track_action_ids: dict[str, str] = {}
    bus_action_ids: dict[str, str] = {}
    created_action_ids: dict[str, str] = {}

    for name in tracks:
        action_id = _action_id("track", name)
        track_action_ids[name] = action_id
        created_action_ids[name.lower()] = action_id
        actions.append(TrackCreateAction(id=action_id, action="track.create", name=name))
        steps.append(
            {
                "id": f"step_{len(steps) + 1}",
                "title": "Create track",
                "description": f"Create a track named {name}.",
            }
        )

    for name in buses:
        action_id = _action_id("bus", name)
        bus_action_ids[name] = action_id
        created_action_ids[name.lower()] = action_id
        actions.append(BusCreateAction(id=action_id, action="bus.create", name=name))
        steps.append(
            {
                "id": f"step_{len(steps) + 1}",
                "title": "Create bus",
                "description": f"Create a bus named {name}.",
            }
        )

    if explicit_routes:
        for source_name, destination_name, pre_fader in explicit_routes:
            source_ref = _entity_ref_for_name(source_name, created_action_ids)
            destination_ref = _entity_ref_for_name(destination_name, created_action_ids)
            action_id = _action_id("send", f"{source_name}_to_{destination_name}")
            actions.append(
                SendCreateAction(
                    id=action_id,
                    action="send.create",
                    source=source_ref,
                    destination=destination_ref,
                    mode="pre-fader" if pre_fader else "post-fader",
                )
            )
            steps.append(
                {
                    "id": f"step_{len(steps) + 1}",
                    "title": "Create send",
                    "description": f"Route {source_name} into {destination_name}.",
                }
            )
    if should_route and not explicit_routes:
        destination_name = buses[0] if buses else None
        if destination_name is None:
            raise ValueError("routing requires a destination bus")
        destination_ref = EntityRef(action_id=bus_action_ids[destination_name])
        route_tracks = [name for name in tracks if name != destination_name]
        if not route_tracks:
            raise ValueError("routing requires at least one source track")
        for name in route_tracks:
            source_ref = EntityRef(action_id=track_action_ids[name])
            action_id = _action_id("send", f"{name}_to_{destination_name}")
            actions.append(
                SendCreateAction(
                    id=action_id,
                    action="send.create",
                    source=source_ref,
                    destination=destination_ref,
                    mode="pre-fader" if "pre-fader" in prompt.lower() else "post-fader",
                )
            )
            steps.append(
                {
                    "id": f"step_{len(steps) + 1}",
                    "title": "Create send",
                    "description": f"Route {name} into {destination_name}.",
                }
            )

    summary = _build_summary(tracks, buses, should_route)
    warnings = [] if actions else ["No executable actions were derived from the prompt."]
    return SessionBuilderPlan(
        summary=summary,
        steps=steps,
        warnings=warnings,
        requires_confirmation=True,
        actions=actions,
    )


def compile_session_plan(plan: SessionBuilderPlan) -> list[BridgePlanStep]:
    created_names = {
        action.id: action.name
        for action in plan.actions
        if action.action in {"track.create", "bus.create"}
    }
    compiled: list[BridgePlanStep] = []
    for action in plan.actions:
        if action.action == "track.create":
            compiled.append(BridgePlanStep(tool="create_track", args={"name": action.name}))
            continue
        if action.action == "bus.create":
            compiled.append(BridgePlanStep(tool="create_bus", args={"name": action.name}))
            continue
        if action.action == "send.create":
            compiled.append(
                BridgePlanStep(
                    tool="create_send",
                    args={
                        "src": _resolve_entity_ref(action.source, created_names),
                        "dst": _resolve_entity_ref(action.destination, created_names),
                        "pre_fader": action.mode == "pre-fader",
                    },
                )
            )
            continue
        if action.action == "fx.insert":
            compiled.append(
                BridgePlanStep(
                    tool="insert_fx",
                    args={
                        "track_ref": _resolve_entity_ref(action.target, created_names),
                        "fx_name": action.fx_name,
                    },
                )
            )
            continue
        raise ValueError(f"unsupported action for executor: {action.action}")
    return compiled


def _extract_named_creations(prompt: str) -> tuple[list[str], list[str]]:
    normalized = prompt.strip()
    named_tracks, named_buses = _extract_explicit_named_clauses(normalized)

    if named_tracks or named_buses:
        return named_tracks, named_buses

    if not normalized.lower().startswith("create "):
        return [], []

    parts = _split_create_clause(normalized[7:])
    tracks: list[str] = []
    buses: list[str] = []
    for part in parts:
        cleaned = _clean_name(part)
        if not cleaned:
            continue
        cleaned = _clean_name(re.sub(r"^(?:Track|Bus)\s+", "", cleaned, flags=re.IGNORECASE))
        if _looks_like_bus(cleaned):
            buses.append(_strip_suffix(cleaned, "bus"))
        else:
            tracks.append(cleaned)
    return _dedupe(tracks), _dedupe(buses)


def _extract_explicit_named_clauses(prompt: str) -> tuple[list[str], list[str]]:
    tracks: list[str] = []
    buses: list[str] = []
    pattern = r"\b(?:create\s+)?(?:a\s+|an\s+)?(track|bus)\s+(?:named|called)\s+([^,.]+?)(?=(?:\s+and\s+(?:a\s+|an\s+)?(?:track|bus)\s+(?:named|called)\b)|(?:\s+and\s+route\b)|(?:\s*,)|(?:\s*\.)|$)"
    for kind, raw_name in re.findall(pattern, prompt, flags=re.IGNORECASE):
        cleaned = _clean_name(raw_name)
        if not cleaned:
            continue
        if kind.lower() == "bus":
            buses.append(_strip_suffix(cleaned, "bus"))
        else:
            tracks.append(cleaned)
    return _dedupe(tracks), _dedupe(buses)


def _extract_named_entities(prompt: str, kind: str) -> list[str]:
    pattern = rf"\b(?:create\s+)?(?:a\s+|an\s+)?{kind}\s+(?:named|called)\s+([^,.]+?)(?=(?:\s+and\b)|(?:\s*,)|$)"
    matches = re.findall(pattern, prompt, flags=re.IGNORECASE)
    return _dedupe([_clean_name(match) for match in matches if _clean_name(match)])


def _split_create_clause(text: str) -> list[str]:
    normalized = re.sub(r"\b(?:then\s+)?route\b.*$", "", text, flags=re.IGNORECASE).strip(" .")
    if not normalized:
        return []
    normalized = re.sub(r"\band\b", ",", normalized, flags=re.IGNORECASE)
    return [part.strip() for part in normalized.split(",") if part.strip()]


def _wants_routing(prompt: str) -> bool:
    lowered = prompt.lower()
    return " route " in f" {lowered} " or "send " in lowered


def _extract_route_pairs(prompt: str) -> list[tuple[str, str, bool]]:
    pairs: list[tuple[str, str, bool]] = []
    patterns = (
        r"\broute\s+(.+?)\s+to\s+(.+?)(?=$|,|\s+then\b|\.)",
        r"\bsend\s+(.+?)\s+to\s+(.+?)(?=$|,|\s+then\b|\.)",
    )
    lowered = prompt.lower()
    for pattern in patterns:
        for src_raw, dst_raw in re.findall(pattern, prompt, flags=re.IGNORECASE):
            src = _clean_name(src_raw)
            dst = _clean_name(re.sub(r"\bpre-fader\b", "", dst_raw, flags=re.IGNORECASE).strip())
            if src and dst and not _is_generic_route_ref(src) and not _is_generic_route_ref(dst):
                pairs.append((src, dst, "pre-fader" in lowered))
    return pairs


def _infer_bus_name(tracks: list[str]) -> str | None:
    if not tracks:
        return None
    if len(tracks) == 1:
        return f"{tracks[0]} Bus"
    return "Mix Bus"


def _build_summary(tracks: list[str], buses: list[str], should_route: bool) -> str:
    parts = []
    if tracks:
        parts.append("create " + ", ".join(tracks))
    if buses:
        parts.append("create " + ", ".join(buses))
    if should_route:
        parts.append("route requested tracks")
    summary = "; ".join(parts)
    return summary[:1].upper() + summary[1:] + "." if summary else "No supported actions."


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _clean_name(raw: str) -> str:
    text = raw.strip().strip(".,")
    text = re.sub(r"^(?:a|an|the)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:named|called)\s+", "", text, flags=re.IGNORECASE)
    words = [word for word in re.split(r"\s+", text) if word]
    return " ".join(word if word.isupper() else word.capitalize() for word in words)


def _looks_like_bus(name: str) -> bool:
    return name.lower().endswith(" bus") or name.lower() == "bus"


def _entity_ref_for_name(name: str, created_action_ids: dict[str, str]) -> EntityRef:
    action_id = created_action_ids.get(name.lower())
    if action_id:
        return EntityRef(action_id=action_id)
    return EntityRef(name=name)


def _is_generic_route_ref(name: str) -> bool:
    normalized = name.lower().strip()
    generic_refs = {
        "all vocals",
        "vocals",
        "all tracks",
        "tracks",
        "the bus",
        "bus",
    }
    return normalized in generic_refs


def _strip_suffix(name: str, suffix: str) -> str:
    if name.lower().endswith(f" {suffix.lower()}"):
        return name
    return f"{name} {suffix.title()}"


def _action_id(prefix: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return f"{prefix}_{slug}"


def _resolve_entity_ref(ref: EntityRef, created_names: dict[str, str]) -> dict[str, int | str]:
    if ref.track_id is not None:
        return {"type": "track_id", "value": ref.track_id}
    if ref.name:
        return {"type": "track_name", "value": ref.name}
    if ref.action_id:
        if ref.action_id not in created_names:
            raise ValueError(f"unknown action reference '{ref.action_id}'")
        return {"type": "track_name", "value": created_names[ref.action_id]}
    raise ValueError("entity ref is missing a selector")
