from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from companion.models.schemas import ClarificationOption, ClarificationPrompt, PlanResponse, PlanStep

LOGGER = logging.getLogger(__name__)

ALLOWED_TOOLS: dict[str, set[str]] = {
    'create_bus': set(),
    'create_send': {'src', 'dst'},
    'insert_fx': {'track_ref', 'fx_name'},
    'create_track': set(),
    'transport.play': set(),
    'transport.stop': set(),
    'project.set_tempo': {'bpm'},
    'track.set_color': {'color'},
    'set_track_color': {'color'},
}

DRUM_KEYWORDS = ('kick', 'snare', 'tom', 'drum', 'hat', 'overhead', 'room')
GENERIC_FX_MAP = {
    'eq': 'ReaEQ',
    'reaeq': 'ReaEQ',
    'compressor': 'ReaComp',
    'comp': 'ReaComp',
    'reacomp': 'ReaComp',
    'reverb': 'ReaVerbate',
    'reaverbate': 'ReaVerbate',
}


def validate_plan_steps(steps: Iterable[PlanStep]) -> list[str]:
    errors: list[str] = []
    for idx, step in enumerate(steps):
        requirements = ALLOWED_TOOLS.get(step.tool)
        if requirements is None:
            errors.append(f"unknown tool '{step.tool}' at index {idx}")
            continue
        missing = requirements - set(step.args.keys())
        if missing:
            errors.append(f"tool '{step.tool}' missing args {sorted(missing)}")
    return errors


def _normalize_prompt(prompt: str) -> str:
    return (prompt or '').strip().lower()


def _extract_tempo(prompt: str) -> float | None:
    match = re.search(r'\b(?:tempo|bpm)\s*(?:to\s*)?(\d+(?:\.\d+)?)\b', prompt.lower())
    if not match:
        return None
    bpm = float(match.group(1))
    return bpm if bpm > 0 else None


def _resolve_track_ref(track: dict[str, Any]) -> dict[str, Any] | None:
    track_id = track.get('id') or track.get('index')
    if track_id is not None:
        return {'type': 'track_id', 'value': track_id}
    name = track.get('name')
    if name:
        return {'type': 'track_name', 'value': name}
    return None


def _title_case_name(raw: str) -> str:
    words = [word for word in re.split(r'\s+', raw.strip()) if word]
    return ' '.join(word if word.isupper() else word.capitalize() for word in words)


def _clean_name(raw: str) -> str:
    text = raw.strip().strip('.,')
    text = re.sub(r'^(?:a|an|the)\s+', '', text, flags=re.IGNORECASE)
    return _title_case_name(text)


def _track_name_ref(name: str) -> dict[str, Any]:
    return {'type': 'track_name', 'value': _clean_name(name)}


def _state_tracks(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(state, dict):
        return []
    tracks = state.get('tracks', [])
    return tracks if isinstance(tracks, list) else []


def _has_track_named(state: dict[str, Any] | None, name: str) -> bool:
    expected = _clean_name(name).lower()
    for track in _state_tracks(state):
        if str(track.get('name') or '').lower() == expected:
            return True
    return False


def _ensure_create_step(steps: list[PlanStep], tool: str, name: str) -> None:
    cleaned = _clean_name(name)
    for step in steps:
        if step.tool == tool and _clean_name(str(step.args.get('name') or '')) == cleaned:
            return
    steps.append(PlanStep(tool=tool, args={'name': cleaned}))


def _clarification_response(prompt_id: str, question: str, options: list[str], summary: str) -> PlanResponse:
    return PlanResponse(
        ok=True,
        summary=summary,
        source='heuristic',
        requires_clarification=True,
        clarification=ClarificationPrompt(
            id=prompt_id,
            question=question,
            options=[ClarificationOption(value=option, label=option) for option in options],
        ),
        steps=[],
    )


def _extract_named_entities(prompt: str, kind: str) -> list[str]:
    pattern = rf'\bcreate\s+(?:a\s+|an\s+)?{kind}\s+(?:called|named)?\s*([^,.]+?)(?=(?:\s+and\s+(?:a\s+|an\s+)?(?:track|bus)\b)|(?:\s+then\b)|(?:\s*,)|$)'
    matches = re.findall(pattern, prompt, flags=re.IGNORECASE)
    return [_clean_name(match) for match in matches if _clean_name(match)]


def _extract_create_pairs(prompt: str) -> tuple[list[str], list[str]]:
    tracks = _extract_named_entities(prompt, 'track')
    buses = _extract_named_entities(prompt, 'bus')
    paired = re.search(r'\bcreate\s+([^,.]+?)\s+and\s+([^,.]+?)(?=(?:\s+then\b)|(?:\s*,)|$)', prompt, flags=re.IGNORECASE)
    if paired:
        left = paired.group(1)
        right = paired.group(2)
        if re.search(r'\btrack\b', left, flags=re.IGNORECASE) and re.search(r'\bbus\b', right, flags=re.IGNORECASE):
            tracks.extend(_extract_named_entities('create ' + left, 'track'))
            buses.extend(_extract_named_entities('create ' + right, 'bus'))
    return tracks, buses


def _infer_vocal_track_bus(prompt: str) -> tuple[str | None, str | None]:
    if 'vocal track' not in prompt and 'voice track' not in prompt:
        return None, None
    base = 'Voice' if 'voice track' in prompt else 'Vocal'
    track_name = 'Lead Vocal' if 'lead vocal' in prompt else base
    bus_name = track_name + ' Bus'
    if 'voice bus' in prompt:
        bus_name = 'Voice Bus'
    elif 'vocal bus' in prompt:
        bus_name = 'Vocal Bus'
    return track_name, bus_name


def _extract_route_pairs(prompt: str) -> list[tuple[str, str, bool]]:
    pairs: list[tuple[str, str, bool]] = []
    patterns = (
        r'\broute\s+(.+?)\s+to\s+(.+?)(?=$|,|\s+then\b)',
        r'\bsend\s+(.+?)\s+to\s+(.+?)(?=$|,|\s+then\b)',
    )
    for pattern in patterns:
        for src_raw, dst_raw in re.findall(pattern, prompt, flags=re.IGNORECASE):
            src = _clean_name(src_raw)
            dst = _clean_name(dst_raw.replace('pre-fader', '').strip())
            if src and dst:
                pairs.append((src, dst, 'pre-fader' in prompt.lower()))
    return pairs


def _find_track_by_hint(state: dict[str, Any] | None, hint: str) -> dict[str, Any] | None:
    normalized = _clean_name(hint).lower()
    for track in _state_tracks(state):
        if str(track.get('name') or '').lower() == normalized:
            return track
    for track in _state_tracks(state):
        if normalized in str(track.get('name') or '').lower():
            return track
    return None


def _find_matching_tracks(state: dict[str, Any] | None, hint: str) -> list[dict[str, Any]]:
    normalized = _clean_name(hint).lower()
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for track in _state_tracks(state):
        name = str(track.get('name') or '').lower()
        if name == normalized:
            exact.append(track)
        elif normalized and normalized in name:
            partial.append(track)
    return exact or partial


def _resolve_track_ref_or_name(state: dict[str, Any] | None, name: str) -> dict[str, Any]:
    track = _find_track_by_hint(state, name)
    return _resolve_track_ref(track) if track else _track_name_ref(name)


def _resolve_track_or_clarify(
    state: dict[str, Any] | None,
    name: str,
    clarification_id: str,
    answers: dict[str, str] | None,
    question: str,
    summary: str,
) -> tuple[dict[str, Any] | None, PlanResponse | None]:
    answer = (answers or {}).get(clarification_id)
    if answer:
        return _track_name_ref(answer), None
    matches = _find_matching_tracks(state, name)
    if len(matches) == 1:
        track = matches[0]
        return _resolve_track_ref(track), None
    if len(matches) == 0:
        available = [str(track.get('name') or '') for track in _state_tracks(state) if track.get('name')]
        if available:
            return None, _clarification_response(
                clarification_id,
                f"I couldn't find a track matching '{name}'. Which track should I use?",
                available,
                summary,
            )
        return None, PlanResponse(
            ok=False,
            summary=f"I couldn't find a track matching '{name}' in the current project state.",
            source='heuristic',
            steps=[],
        )
    options = [str(track.get('name') or '') for track in matches if track.get('name')]
    return None, _clarification_response(clarification_id, question, options, summary)


def _extract_fx_target(prompt: str) -> tuple[str, str] | None:
    patterns = (
        r'\badd\s+([a-z0-9]+)\s+to\s+([^,.]+?)(?=$|,|\s+then\b)',
        r'\bput\s+([a-z0-9]+)\s+on\s+([^,.]+?)(?=$|,|\s+then\b)',
        r'\binsert\s+([a-z0-9]+)\s+on\s+([^,.]+?)(?=$|,|\s+then\b)',
    )
    for pattern in patterns:
        match = re.search(pattern, prompt, flags=re.IGNORECASE)
        if match:
            fx_name = GENERIC_FX_MAP.get(match.group(1).lower(), _clean_name(match.group(1)))
            target = _clean_name(match.group(2))
            if fx_name and target:
                return fx_name, target
    return None


def _plan_explicit_creation(prompt: str) -> PlanResponse | None:
    tracks, buses = _extract_create_pairs(prompt)
    normalized = _normalize_prompt(prompt)
    if not tracks and re.search(r'\bcreate\s+(?:a\s+|an\s+)?track\b', normalized):
        tracks.append('')
    if not buses and re.search(r'\bcreate\s+(?:a\s+|an\s+)?bus\b', normalized):
        buses.append('')
    if not tracks and not buses:
        return None
    steps: list[PlanStep] = []
    for track_name in tracks:
        if track_name:
            _ensure_create_step(steps, 'create_track', track_name)
        else:
            steps.append(PlanStep(tool='create_track', args={}))
    for bus_name in buses:
        if bus_name:
            _ensure_create_step(steps, 'create_bus', bus_name)
        else:
            steps.append(PlanStep(tool='create_bus', args={}))
    return PlanResponse(ok=True, summary='Create the requested track and bus structure.', source='heuristic', steps=steps)


def _plan_route_prompt(prompt: str, state: dict[str, Any] | None, clarification_answers: dict[str, str] | None) -> PlanResponse | None:
    route_pairs = _extract_route_pairs(prompt)
    if not route_pairs:
        return None
    steps: list[PlanStep] = []
    for src_name, dst_name, pre_fader in route_pairs:
        src_ref, clarification = _resolve_track_or_clarify(
            state,
            src_name,
            'route_src_track',
            clarification_answers,
            f"I found multiple tracks matching '{src_name}'. Which source track should I route?",
            'Clarification needed before creating the requested send.',
        )
        if clarification:
            return clarification
        dst_ref, clarification = _resolve_track_or_clarify(
            state,
            dst_name,
            'route_dst_track',
            clarification_answers,
            f"I found multiple tracks matching '{dst_name}'. Which destination track should I use?",
            'Clarification needed before creating the requested send.',
        )
        if clarification:
            return clarification
        steps.append(PlanStep(tool='create_send', args={'src': src_ref, 'dst': dst_ref, 'pre_fader': pre_fader}))
    return PlanResponse(ok=True, summary='Route the requested track sends.', source='heuristic', steps=steps)


def _plan_fx_insert(prompt: str, state: dict[str, Any] | None, clarification_answers: dict[str, str] | None) -> PlanResponse | None:
    parsed = _extract_fx_target(prompt)
    if parsed is None:
        return None
    fx_name, target = parsed
    track_ref, clarification = _resolve_track_or_clarify(
        state,
        target,
        'fx_target_track',
        clarification_answers,
        f"I found multiple tracks matching '{target}'. Which one should I use?",
        'Clarification needed before inserting FX.',
    )
    if clarification:
        return clarification
    return PlanResponse(
        ok=True,
        summary=f'Insert {fx_name} on {target}.',
        source='heuristic',
        steps=[PlanStep(tool='insert_fx', args={'track_ref': track_ref, 'fx_name': fx_name})],
    )


def _plan_basic_vocal_setup(prompt: str, state: dict[str, Any] | None) -> PlanResponse | None:
    if 'basic vocal setup' not in prompt and 'create a vocal track and a vocal bus' not in prompt:
        return None
    track_name, bus_name = _infer_vocal_track_bus(prompt)
    track_name = track_name or 'Lead Vocal'
    bus_name = bus_name or 'Vocal Bus'
    steps: list[PlanStep] = []
    if not _has_track_named(state, track_name):
        _ensure_create_step(steps, 'create_track', track_name)
    if not _has_track_named(state, bus_name):
        _ensure_create_step(steps, 'create_bus', bus_name)
    steps.append(PlanStep(tool='create_send', args={'src': _track_name_ref(track_name), 'dst': _track_name_ref(bus_name)}))
    steps.append(PlanStep(tool='insert_fx', args={'track_ref': _track_name_ref(track_name), 'fx_name': 'ReaEQ'}))
    steps.append(PlanStep(tool='insert_fx', args={'track_ref': _track_name_ref(track_name), 'fx_name': 'ReaComp'}))
    return PlanResponse(ok=True, summary='Create a basic vocal track and bus with routing and core FX.', source='heuristic', steps=steps)


def _plan_drum_bus(prompt: str, state: dict[str, Any] | None) -> PlanResponse | None:
    if 'drum bus' not in prompt:
        return None
    tracks: list[dict[str, Any]] = []
    for track in _state_tracks(state):
        name = (track.get('name') or '').lower()
        if any(keyword in name for keyword in DRUM_KEYWORDS):
            tracks.append(track)
    steps: list[PlanStep] = [PlanStep(tool='create_bus', args={'name': 'Drum Bus'})]
    refs: list[dict[str, Any]] = []
    for track in tracks:
        ref = _resolve_track_ref(track)
        if ref:
            refs.append(ref)
    existing_names = {track.get('name') for track in tracks if track.get('name')}
    for keyword in DRUM_KEYWORDS:
        if keyword in prompt and keyword.capitalize() not in existing_names:
            refs.append({'type': 'track_name', 'value': keyword.capitalize()})
            existing_names.add(keyword.capitalize())
    for ref in refs:
        steps.append(PlanStep(tool='create_send', args={'src': ref, 'dst': {'type': 'track_name', 'value': 'Drum Bus'}}))
    return PlanResponse(ok=True, summary='Create a Drum Bus and route the detected drum tracks into it.', source='heuristic', steps=steps)


def _plan_vocal_session(prompt: str) -> PlanResponse | None:
    if 'vocal session' not in prompt:
        return None
    steps: list[PlanStep] = []
    for name in ['Lead Vocal', 'Double L', 'Double R']:
        steps.append(PlanStep(tool='create_track', args={'name': name}))
    steps.append(PlanStep(tool='create_bus', args={'name': 'Vocal Bus'}))
    steps.append(PlanStep(tool='create_bus', args={'name': 'Reverb Bus'}))
    steps.append(PlanStep(tool='insert_fx', args={'track_ref': {'type': 'track_name', 'value': 'Lead Vocal'}, 'fx_name': 'ReaEQ'}))
    steps.append(PlanStep(tool='insert_fx', args={'track_ref': {'type': 'track_name', 'value': 'Lead Vocal'}, 'fx_name': 'ReaComp'}))
    for singer in ['Lead Vocal', 'Double L', 'Double R']:
        steps.append(PlanStep(tool='create_send', args={'src': {'type': 'track_name', 'value': singer}, 'dst': {'type': 'track_name', 'value': 'Vocal Bus'}}))
    steps.append(PlanStep(tool='create_send', args={'src': {'type': 'track_name', 'value': 'Vocal Bus'}, 'dst': {'type': 'track_name', 'value': 'Reverb Bus'}}))
    return PlanResponse(ok=True, summary='Create a vocal session template with buses, FX, and routing.', source='heuristic', steps=steps)


def _plan_transport(prompt: str) -> PlanResponse | None:
    wants_play = 'play' in prompt and 'stop' not in prompt
    wants_stop = 'stop' in prompt and 'play' not in prompt
    if 'play' in prompt and 'stop' in prompt:
        steps = [PlanStep(tool='transport.play', args={}), PlanStep(tool='transport.stop', args={})]
        return PlanResponse(ok=True, summary='Play and stop the transport.', source='heuristic', steps=steps)
    steps: list[PlanStep] = []
    if wants_play:
        steps.append(PlanStep(tool='transport.play', args={}))
    if wants_stop:
        steps.append(PlanStep(tool='transport.stop', args={}))
    if steps:
        return PlanResponse(ok=True, summary='Control playback.', source='heuristic', steps=steps)
    return None


def _plan_tempo(prompt: str) -> PlanResponse | None:
    bpm = _extract_tempo(prompt)
    if bpm is None:
        return None
    return PlanResponse(ok=True, summary=f'Set project tempo to {bpm:.0f} BPM.', source='heuristic', steps=[PlanStep(tool='project.set_tempo', args={'bpm': bpm})])


def plan_prompt_to_actions(
    prompt: str,
    project_state: dict[str, Any] | None = None,
    clarification_answers: dict[str, str] | None = None,
) -> PlanResponse:
    cleaned = (prompt or '').strip()
    if not cleaned:
        raise ValueError('prompt must not be empty')

    normalized = _normalize_prompt(cleaned)
    candidates = (
        _plan_vocal_session(normalized),
        _plan_basic_vocal_setup(normalized, project_state),
        _plan_drum_bus(normalized, project_state),
        _plan_explicit_creation(cleaned),
        _plan_route_prompt(cleaned, project_state, clarification_answers),
        _plan_fx_insert(cleaned, project_state, clarification_answers),
        _plan_tempo(cleaned),
        _plan_transport(normalized),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        if candidate.requires_clarification:
            return candidate
        errors = validate_plan_steps(candidate.steps)
        if errors:
            LOGGER.warning('planner produced invalid steps: %s', errors)
            continue
        return candidate

    return PlanResponse(ok=False, summary='No matching workflow yet. This is where an LLM-backed planner will plug in.', source='unsupported', steps=[])
