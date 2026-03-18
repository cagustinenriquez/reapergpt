from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from companion.models.schemas import PlanResponse, PlanStep

LOGGER = logging.getLogger(__name__)

ALLOWED_TOOLS: dict[str, set[str]] = {
    'create_bus': {'name'},
    'create_send': {'src', 'dst'},
    'insert_fx': {'track_ref', 'fx_name'},
    'create_track': set(),
    'transport.play': set(),
    'transport.stop': set(),
    'project.set_tempo': {'bpm'},
    'track.set_color': {'color'},
    'set_track_color': {'color'},
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


def _plan_drum_bus(prompt: str, state: dict[str, Any] | None) -> PlanResponse | None:
    if 'drum bus' not in prompt:
        return None
    tracks: list[dict[str, Any]] = []
    for track in (state or {}).get('tracks', []):
        name = (track.get('name') or '').lower()
        if any(keyword in name for keyword in ('kick', 'snare', 'tom', 'drum', 'hat', 'overhead')):
            tracks.append(track)
    steps: list[PlanStep] = [PlanStep(tool='create_bus', args={'name': 'Drum Bus'})]
    refs: list[dict[str, Any]] = []
    for track in tracks:
        ref = _resolve_track_ref(track)
        if ref:
            refs.append(ref)
    existing_names = {track.get('name') for track in tracks if track.get('name')}
    for keyword in ('kick', 'snare', 'tom', 'hat', 'overhead'):
        if keyword in prompt and keyword.capitalize() not in existing_names:
            refs.append({'type': 'track_name', 'value': keyword.capitalize()})
            existing_names.add(keyword.capitalize())
    for ref in refs:
        steps.append(
            PlanStep(
                tool='create_send',
                args={
                    'src': ref,
                    'dst': {'type': 'track_name', 'value': 'Drum Bus'},
                },
            )
        )
    return PlanResponse(
        ok=True,
        summary='Create a Drum Bus and route the detected drum tracks into it.',
        source='heuristic',
        steps=steps,
    )


def _plan_vocal_session(prompt: str) -> PlanResponse | None:
    if 'vocal session' not in prompt:
        return None
    steps: list[PlanStep] = []
    track_names = ['Lead Vocal', 'Double L', 'Double R', 'Vocal Bus', 'Reverb Bus']
    for name in track_names:
        steps.append(PlanStep(tool='create_track', args={'name': name}))
    steps.append(
        PlanStep(
            tool='insert_fx',
            args={
                'track_ref': {'type': 'track_name', 'value': 'Lead Vocal'},
                'fx_name': 'ReaEQ',
            },
        )
    )
    steps.append(
        PlanStep(
            tool='insert_fx',
            args={
                'track_ref': {'type': 'track_name', 'value': 'Lead Vocal'},
                'fx_name': 'ReaComp',
            },
        )
    )
    steps.append(PlanStep(tool='create_bus', args={'name': 'Vocal Bus'}))
    steps.append(PlanStep(tool='create_bus', args={'name': 'Reverb Bus'}))
    for singer in ['Lead Vocal', 'Double L', 'Double R']:
        steps.append(
            PlanStep(
                tool='create_send',
                args={
                    'src': {'type': 'track_name', 'value': singer},
                    'dst': {'type': 'track_name', 'value': 'Vocal Bus'},
                },
            )
        )
    steps.append(
        PlanStep(
            tool='create_send',
            args={
                'src': {'type': 'track_name', 'value': 'Vocal Bus'},
                'dst': {'type': 'track_name', 'value': 'Reverb Bus'},
            },
        )
    )
    return PlanResponse(
        ok=True,
        summary='Create a vocal session template with buses, FX, and routing.',
        source='heuristic',
        steps=steps,
    )


def _plan_transport(prompt: str) -> PlanResponse | None:
    wants_play = 'play' in prompt and 'stop' not in prompt
    wants_stop = 'stop' in prompt and 'play' not in prompt
    if 'play' in prompt and 'stop' in prompt:
        wants_play = False
        wants_stop = False
        steps = [
            PlanStep(tool='transport.play', args={}),
            PlanStep(tool='transport.stop', args={}),
        ]
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
    return PlanResponse(
        ok=True,
        summary=f'Set project tempo to {bpm:.0f} BPM.',
        source='heuristic',
        steps=[PlanStep(tool='project.set_tempo', args={'bpm': bpm})],
    )


def plan_prompt_to_actions(prompt: str, project_state: dict[str, Any] | None = None) -> PlanResponse:
    cleaned = (prompt or '').strip()
    if not cleaned:
        raise ValueError('prompt must not be empty')

    normalized = _normalize_prompt(cleaned)
    candidates = (
        _plan_vocal_session(normalized),
        _plan_drum_bus(normalized, project_state),
        _plan_tempo(cleaned),
        _plan_transport(normalized),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        errors = validate_plan_steps(candidate.steps)
        if errors:
            LOGGER.warning('planner produced invalid steps: %s', errors)
            continue
        return candidate

    return PlanResponse(
        ok=False,
        summary='No matching workflow yet. This is where an LLM-backed planner will plug in.',
        source='unsupported',
        steps=[],
    )
