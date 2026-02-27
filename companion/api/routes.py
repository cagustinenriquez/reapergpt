from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from typing import Union
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from companion.config import Settings, get_settings
from companion.daws.reaper.client import ReaperBridgeClient
from companion.llm.planner import plan_prompt_to_actions
from companion.models.actions import ActionBatch, ReaperAction
from companion.models.envelope import (
    ExecutePlanRequest,
    ExecutePlanResponse,
    ExecutePlanStepResult,
    PlanRequest,
    PlanResponse,
    ProjectStateResponse,
    PromptClarificationQuestion,
    PromptClarificationResponse,
    PromptContinueRequest,
    PromptDispatchRequest,
    SubmitActionsRequest,
    SubmitActionsResponse,
)
from companion.services.action_dispatcher import ActionDispatcher

router = APIRouter()
_prompt_sessions: dict[str, dict[str, Any]] = {}
_plan_sessions: dict[str, dict[str, Any]] = {}
_plan_sessions_loaded_for_path: str | None = None


def get_reaper_client(settings: Settings = Depends(get_settings)) -> ReaperBridgeClient:
    return ReaperBridgeClient(
        base_url=settings.reaper_bridge_url,
        dry_run=settings.bridge_dry_run,
        transport=settings.reaper_bridge_transport,
        bridge_dir=settings.reaper_bridge_dir or None,
    )


def get_dispatcher(client: ReaperBridgeClient = Depends(get_reaper_client)) -> ActionDispatcher:
    return ActionDispatcher(client)


def _plan_session_store_file(settings: Settings) -> Path | None:
    raw = (settings.plan_session_store_path or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def _load_plan_sessions(settings: Settings) -> None:
    global _plan_sessions_loaded_for_path, _plan_sessions
    store_file = _plan_session_store_file(settings)
    store_key = str(store_file) if store_file is not None else ""
    if _plan_sessions_loaded_for_path == store_key:
        return

    _plan_sessions = {}
    _plan_sessions_loaded_for_path = store_key
    if store_file is None or not store_file.exists():
        return

    try:
        raw = store_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return

    sessions = data.get("plan_sessions")
    if not isinstance(sessions, dict):
        return
    sanitized: dict[str, dict[str, Any]] = {}
    for plan_id, payload in sessions.items():
        if not isinstance(plan_id, str) or not isinstance(payload, dict):
            continue
        actions = payload.get("actions")
        if not isinstance(actions, list) or not actions:
            continue
        sanitized[plan_id] = payload
    _plan_sessions = sanitized


def _save_plan_sessions(settings: Settings) -> None:
    store_file = _plan_session_store_file(settings)
    if store_file is None:
        return
    try:
        store_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = store_file.with_suffix(store_file.suffix + ".tmp")
        payload = {"plan_sessions": _plan_sessions}
        tmp_file.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
        tmp_file.replace(store_file)
    except OSError:
        # Non-fatal for MVP: in-memory sessions continue to work.
        return


def _looks_like_garage_band_template(prompt: str) -> bool:
    lower = prompt.lower()
    return "template" in lower and "garage band" in lower


def _extract_template_track_spec(prompt: str) -> list[str]:
    lower = prompt.lower()
    specs: list[str] = []
    ordered_categories = [
        ("Guitar", re.search(r"(\d+)\s+guitars?\b", lower)),
        ("Drumset", re.search(r"(\d+)\s+drum(?:\s*set|sets?)\b", lower)),
        ("Bass", re.search(r"(\d+)\s+bass\b", lower)),
        ("Vocal", re.search(r"(\d+)\s+vocals?\b", lower)),
    ]
    for label, match in ordered_categories:
        if not match:
            continue
        count = max(1, int(match.group(1)))
        if count == 1:
            specs.append(label)
            continue
        for i in range(1, count + 1):
            specs.append(f"{label} {i}")

    if specs:
        return specs
    return ["Guitar 1", "Guitar 2", "Drumset", "Bass", "Vocal"]


def _start_template_clarification(prompt: str) -> PromptClarificationResponse:
    session_id = str(uuid4())
    _prompt_sessions[session_id] = {
        "kind": "garage_band_template",
        "prompt": prompt,
    }
    return PromptClarificationResponse(
        session_id=session_id,
        message="Template request needs a couple of details before building tracks and optional FX.",
        questions=[
            PromptClarificationQuestion(
                key="fx_setup",
                question="Do you want FX setup included? (yes/no)",
            ),
            PromptClarificationQuestion(
                key="sound_style",
                question="What kind of sound do you want? (e.g. clean indie, punk, classic rock, metal, lo-fi)",
            ),
        ],
    )


def _is_truthy_answer(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"y", "yes", "true", "1", "on"}
    if isinstance(value, (int, float)):
        return value != 0
    return False


def _normalize_style(value: Any) -> str:
    if not isinstance(value, str):
        return "classic rock"
    text = value.strip().lower()
    if not text:
        return "classic rock"
    aliases = {
        "clean": "clean indie",
        "indie": "clean indie",
        "punk rock": "punk",
        "rock": "classic rock",
        "classic": "classic rock",
        "lofi": "lo-fi",
    }
    return aliases.get(text, text)


def _track_kind(name: str) -> str:
    lower = name.lower()
    if "guitar" in lower:
        return "guitar"
    if "drum" in lower:
        return "drums"
    if "bass" in lower:
        return "bass"
    if "vocal" in lower:
        return "vocal"
    return "other"


def _starter_fx_for_track(kind: str, style: str) -> list[str]:
    if kind == "guitar":
        if style == "punk":
            return ["ReaEQ (Cockos)", "ReaComp (Cockos)"]
        if style == "metal":
            return ["ReaEQ (Cockos)", "JS: Distortion", "ReaComp (Cockos)"]
        if style == "lo-fi":
            return ["ReaEQ (Cockos)", "JS: Saturation", "ReaDelay (Cockos)"]
        return ["ReaEQ (Cockos)", "ReaComp (Cockos)"]
    if kind == "bass":
        return ["ReaEQ (Cockos)", "ReaComp (Cockos)"]
    if kind == "vocal":
        if style == "lo-fi":
            return ["ReaEQ (Cockos)", "ReaComp (Cockos)", "ReaDelay (Cockos)"]
        return ["ReaEQ (Cockos)", "ReaComp (Cockos)", "ReaVerbate (Cockos)"]
    if kind == "drums":
        return ["ReaEQ (Cockos)", "ReaComp (Cockos)"]
    return []


def _starter_fx_for_bus(bus_name: str, style: str) -> list[str]:
    lower = bus_name.lower()
    if "guitar" in lower and style == "metal":
        return ["ReaComp (Cockos)", "JS: Saturation"]
    if "vocal" in lower and style in {"clean indie", "classic rock"}:
        return ["ReaComp (Cockos)", "ReaVerbate (Cockos)"]
    if style == "lo-fi":
        return ["ReaComp (Cockos)", "ReaEQ (Cockos)"]
    return ["ReaComp (Cockos)"]


def _build_garage_band_template_batch(prompt: str, answers: dict[str, Any]) -> ActionBatch:
    track_names = _extract_template_track_spec(prompt)
    include_fx = _is_truthy_answer(answers.get("fx_setup"))
    style = _normalize_style(answers.get("sound_style"))
    actions: list[ReaperAction] = []

    kinds_seen: set[str] = set()
    for name in track_names:
        kind = _track_kind(name)
        kinds_seen.add(kind)
        actions.append(ReaperAction(type="track.create", params={"name": name}))
        if not include_fx:
            continue
        for fx_name in _starter_fx_for_track(kind, style):
            actions.append(ReaperAction(type="fx.add", params={"track_ref": "last_created", "fx_name": fx_name}))

    if include_fx:
        bus_names: list[str] = []
        if "drums" in kinds_seen:
            bus_names.append("Drum Bus")
        if "guitar" in kinds_seen:
            bus_names.append("Guitar Bus")
        if "vocal" in kinds_seen:
            bus_names.append("Vocal Bus")

        for bus_name in bus_names:
            actions.append(ReaperAction(type="track.create", params={"name": bus_name}))
            for fx_name in _starter_fx_for_bus(bus_name, style):
                actions.append(ReaperAction(type="fx.add", params={"track_ref": "last_created", "fx_name": fx_name}))

    return ActionBatch(actions=actions)


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "service": "reapergpt-companion",
        "bridge": {
            "url": settings.reaper_bridge_url,
            "mode": settings.reaper_bridge_transport,
            "dir": settings.reaper_bridge_dir or None,
        },
        "planner": {
            "provider": settings.llm_provider,
            "ollama_base_url": settings.ollama_base_url if settings.llm_provider.strip().lower() == "ollama" else None,
            "ollama_model": settings.ollama_model if settings.llm_provider.strip().lower() == "ollama" else None,
            "allow_heuristic_fallback": settings.llm_allow_heuristic_fallback,
        },
    }


@router.get("/state/project", response_model=ProjectStateResponse)
def get_project_state(
    client: ReaperBridgeClient = Depends(get_reaper_client),
) -> ProjectStateResponse:
    try:
        payload = client.get_project_state()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Project state unavailable: {exc}") from exc
    return ProjectStateResponse.model_validate(payload)


@router.post("/actions", response_model=SubmitActionsResponse)
def submit_actions(
    payload: SubmitActionsRequest,
    dispatcher: ActionDispatcher = Depends(get_dispatcher),
) -> SubmitActionsResponse:
    return dispatcher.dispatch_batch(payload.batch)


@router.post("/plan", response_model=PlanResponse)
def create_plan(
    payload: PlanRequest,
    settings: Settings = Depends(get_settings),
    client: ReaperBridgeClient = Depends(get_reaper_client),
) -> PlanResponse:
    _load_plan_sessions(settings)
    project_state: dict[str, Any] | None = None
    project_state_included = False
    state_error: str | None = None
    if payload.include_project_state:
        try:
            state_payload = client.get_project_state()
            project_state = state_payload.get("project") if isinstance(state_payload, dict) else None
            project_state_included = isinstance(project_state, dict)
        except Exception as exc:
            state_error = str(exc)

    planned = plan_prompt_to_actions(
        payload.goal,
        settings,
        project_state=project_state,
        allow_heuristic_fallback=settings.llm_allow_heuristic_fallback,
    )
    actions = planned.batch.actions if planned.batch is not None else []

    rationale = []
    assumptions = []
    risks = []
    clarifying_questions = []

    if actions:
        rationale.append(f"Converted goal into {len(actions)} REAPER action(s) using {planned.source} planner output.")
    else:
        rationale.append("No supported REAPER actions could be derived from the goal.")
        clarifying_questions.append("What exact REAPER operation do you want (track, routing, FX, transport, render, or automation)?")

    if project_state_included:
        assumptions.append("Planning used the current REAPER project snapshot available at request time.")
    elif payload.include_project_state:
        risks.append("Plan was created without project state context.")
        if state_error:
            risks.append(f"Project state unavailable: {state_error}")
    else:
        assumptions.append("Planning was requested without project state context.")

    if planned.source == "heuristic":
        risks.append("Heuristic planner may miss intent details compared with LLM planning.")
    elif planned.source == "unsupported":
        risks.append("Goal is outside the currently supported action catalog.")
    elif planned.source == "ollama_error":
        risks.append("LLM planning failed before action generation.")
    if planned.llm_error and planned.source != "ollama":
        risks.append(f"Planner detail: {planned.llm_error}")

    plan_id: str | None = None
    if actions:
        plan_id = str(uuid4())
        _plan_sessions[plan_id] = {
            "goal": payload.goal,
            "actions": [action.model_dump(mode="python") for action in actions],
            "source": planned.source,
        }
        _save_plan_sessions(settings)

    return PlanResponse(
        success=bool(actions),
        source=planned.source,
        proposed_actions=actions,
        rationale=rationale,
        assumptions=assumptions,
        risks=risks,
        clarifying_questions=clarifying_questions,
        project_state_included=project_state_included,
        plan_id=plan_id,
    )


@router.post("/execute-plan", response_model=ExecutePlanResponse)
def execute_plan(
    payload: ExecutePlanRequest,
    settings: Settings = Depends(get_settings),
    dispatcher: ActionDispatcher = Depends(get_dispatcher),
) -> ExecutePlanResponse:
    _load_plan_sessions(settings)
    results: list[ExecutePlanStepResult] = []
    failed_step_index: int | None = None
    mode = dispatcher._client.mode
    if payload.plan_id is not None:
        session = _plan_sessions.get(payload.plan_id)
        if session is None:
            _load_plan_sessions(settings)
            session = _plan_sessions.get(payload.plan_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Plan not found or expired")
        raw_actions = session.get("actions")
        if not isinstance(raw_actions, list) or not raw_actions:
            raise HTTPException(status_code=400, detail="Stored plan has no executable actions")
        actions = [ReaperAction.model_validate(item) for item in raw_actions]
    else:
        actions = payload.actions or []

    for i, action in enumerate(actions, start=1):
        batch_response = dispatcher.dispatch_batch(ActionBatch(actions=[action]))
        mode = batch_response.mode
        if not batch_response.results:
            step_result = ExecutePlanStepResult(
                step_index=i,
                action=action,
                status="rejected",
                detail="No dispatcher result returned for step",
            )
        else:
            item = batch_response.results[0]
            step_result = ExecutePlanStepResult(
                step_index=i,
                action=action,
                status=item.status,
                detail=item.detail,
            )
        results.append(step_result)

        if step_result.status != "accepted":
            failed_step_index = i
            if payload.stop_on_failure:
                break

    return ExecutePlanResponse(
        success=failed_step_index is None,
        mode=mode,
        stop_on_failure=payload.stop_on_failure,
        total_steps=len(actions),
        executed_steps=len(results),
        failed_step_index=failed_step_index,
        results=results,
    )


@router.post("/prompt", response_model=Union[SubmitActionsResponse, PromptClarificationResponse])
def submit_prompt(
    payload: PromptDispatchRequest,
    settings: Settings = Depends(get_settings),
    dispatcher: ActionDispatcher = Depends(get_dispatcher),
) -> Union[SubmitActionsResponse, PromptClarificationResponse]:
    if _looks_like_garage_band_template(payload.prompt):
        return _start_template_clarification(payload.prompt)

    planned = plan_prompt_to_actions(
        payload.prompt,
        settings,
        allow_heuristic_fallback=settings.llm_allow_heuristic_fallback,
    )
    if planned.batch is None:
        if planned.source == "ollama_error" and planned.llm_error:
            raise HTTPException(
                status_code=503,
                detail=f"Ollama planning failed and heuristic fallback is disabled: {planned.llm_error}",
            )
        raise HTTPException(
            status_code=400,
            detail=(
                "Prompt could not be mapped to supported actions yet. "
                "Try: play, stop, tempo 120, solo track 2, mute track 3, "
                "select track 1, create regions for a pop song, create regions for a rock song"
            ),
        )
    dispatched = dispatcher.dispatch_batch(planned.batch)
    return dispatched.model_copy(update={"planner_source": planned.source})


@router.post("/prompt/respond", response_model=SubmitActionsResponse)
def submit_prompt_clarification_response(
    payload: PromptContinueRequest,
    dispatcher: ActionDispatcher = Depends(get_dispatcher),
) -> SubmitActionsResponse:
    session = _prompt_sessions.pop(payload.session_id, None)
    if session is None:
        raise HTTPException(status_code=404, detail="Prompt clarification session not found or expired")

    kind = session.get("kind")
    if kind != "garage_band_template":
        raise HTTPException(status_code=400, detail="Unsupported prompt clarification session type")

    batch = _build_garage_band_template_batch(str(session.get("prompt", "")), payload.answers)
    dispatched = dispatcher.dispatch_batch(batch)
    return dispatched.model_copy(update={"planner_source": "clarification_template"})
