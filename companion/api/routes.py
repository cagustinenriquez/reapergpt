from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from companion.config import Settings, get_settings
from companion.daws.reaper.client import ActionExecutionError, ReaperBridgeClient, get_bridge_client
from companion.llm.planner import plan_prompt_to_actions, validate_plan_steps
from companion.models.schemas import (
    ExecutePlanRequest,
    ExecutePlanResponse,
    PlanRequest,
    PlanResponse,
    PlanStep,
    StepResult,
    VerificationResult,
)

router = APIRouter()
_saved_plans: dict[str, dict[str, Any]] = {}
_expired_plan_ids: dict[str, float] = {}


def _prune_saved_plans(ttl_seconds: float) -> None:
    now = time.monotonic()
    expired_ids: list[str] = []
    for plan_id, payload in _saved_plans.items():
        created_at = float(payload.get("created_at", 0.0))
        if now - created_at > ttl_seconds:
            expired_ids.append(plan_id)
    for plan_id in expired_ids:
        _saved_plans.pop(plan_id, None)
        _expired_plan_ids[plan_id] = now
    for plan_id, expired_at in list(_expired_plan_ids.items()):
        if now - expired_at > ttl_seconds:
            _expired_plan_ids.pop(plan_id, None)


def _track_matches_ref(track: dict[str, Any], ref: dict[str, Any] | None) -> bool:
    if not isinstance(track, dict) or not isinstance(ref, dict):
        return False
    ref_type = ref.get("type")
    ref_value = ref.get("value")
    if ref_type in {"track_id", "track_index"}:
        return track.get("id") == ref_value
    if ref_type == "track_name" and isinstance(ref_value, str):
        return str(track.get("name") or "").lower() == ref_value.lower()
    return False


def _find_track(state: dict[str, Any], ref: dict[str, Any] | None) -> dict[str, Any] | None:
    for track in state.get("tracks", []):
        if _track_matches_ref(track, ref):
            return track
    return None


def _fx_matches(track: dict[str, Any], fx_name: str | None) -> str | None:
    if not fx_name:
        return None
    expected = fx_name.lower()
    for actual in track.get("fx", []):
        if isinstance(actual, str) and expected in actual.lower():
            return actual
    return None


def _send_mode_matches(send: dict[str, Any], pre_fader: bool) -> bool:
    if not isinstance(send, dict):
      return False
    if isinstance(send.get("pre_fader"), bool):
        return send.get("pre_fader") == pre_fader
    mode_name = str(send.get("send_mode_name") or "").lower()
    if mode_name:
        return mode_name == ("pre-fx" if pre_fader else "post-fader")
    mode_value = send.get("send_mode")
    if isinstance(mode_value, (int, float)):
        return int(mode_value) == (1 if pre_fader else 0)
    return not pre_fader


def _verify_steps(steps: list[PlanStep], results: list[StepResult], final_state: dict[str, Any]) -> tuple[list[VerificationResult], list[str]]:
    accepted_by_index = {result.index: result for result in results if result.status in {"accepted", "ok"}}
    verification_results: list[VerificationResult] = []
    verification_errors: list[str] = []

    for index, step in enumerate(steps):
        executed = accepted_by_index.get(index)
        if not executed:
            continue

        if step.tool in {"create_track", "create_bus"}:
            track_ref: dict[str, Any] | None = None
            detail = executed.detail if isinstance(executed.detail, dict) else {}
            if isinstance(detail, dict) and detail.get("track_id") is not None:
                track_ref = {"type": "track_id", "value": detail.get("track_id")}
            elif step.args.get("name"):
                track_ref = {"type": "track_name", "value": step.args.get("name")}
            track = _find_track(final_state, track_ref)
            check_name = "bus_created" if step.tool == "create_bus" else "track_created"
            ok = track is not None
            verification_results.append(
                VerificationResult(
                    index=index,
                    tool=step.tool,
                    check=check_name,
                    ok=ok,
                    expected={"name": step.args.get("name"), "track_id": detail.get("track_id") if isinstance(detail, dict) else None},
                    actual={"track": track} if track else None,
                    message=None if ok else f"Expected {check_name.replace('_', ' ')} was not found in final project state.",
                )
            )
            if not ok:
                verification_errors.append(f"step {index} {check_name}: track missing from final project state")
            continue

        if step.tool == "create_send":
            src_track = _find_track(final_state, step.args.get("src"))
            dst_track = _find_track(final_state, step.args.get("dst"))
            expected_pre_fader = bool(step.args.get("pre_fader"))
            send = None
            if src_track and dst_track:
                for item in final_state.get("sends", []):
                    if (
                        item.get("src") == src_track.get("id")
                        and item.get("dst") == dst_track.get("id")
                        and _send_mode_matches(item, expected_pre_fader)
                    ):
                        send = item
                        break
            ok = send is not None
            verification_results.append(
                VerificationResult(
                    index=index,
                    tool=step.tool,
                    check="send_exists",
                    ok=ok,
                    expected={"src": step.args.get("src"), "dst": step.args.get("dst"), "pre_fader": expected_pre_fader},
                    actual={"send": send, "src_track": src_track, "dst_track": dst_track} if (send or src_track or dst_track) else None,
                    message=None if ok else "Expected send with the requested mode was not found in final project state.",
                )
            )
            if not ok:
                verification_errors.append(f"step {index} send_exists: send missing or send mode mismatch in final project state")
            continue

        if step.tool == "insert_fx":
            track = _find_track(final_state, step.args.get("track_ref"))
            matched_fx = _fx_matches(track or {}, step.args.get("fx_name"))
            ok = track is not None and matched_fx is not None
            verification_results.append(
                VerificationResult(
                    index=index,
                    tool=step.tool,
                    check="fx_inserted",
                    ok=ok,
                    expected={"track_ref": step.args.get("track_ref"), "fx_name": step.args.get("fx_name")},
                    actual={"track": track, "fx_name": matched_fx} if track else None,
                    message=None if ok else "Expected FX was not found on the target track in final project state.",
                )
            )
            if not ok:
                verification_errors.append(f"step {index} fx_inserted: FX missing from final project state")
            continue

        if step.tool == "project.set_tempo":
            expected_bpm = step.args.get("bpm")
            actual_bpm = final_state.get("tempo")
            ok = isinstance(expected_bpm, (int, float)) and isinstance(actual_bpm, (int, float)) and abs(float(actual_bpm) - float(expected_bpm)) < 0.001
            verification_results.append(
                VerificationResult(
                    index=index,
                    tool=step.tool,
                    check="tempo_changed",
                    ok=ok,
                    expected={"bpm": expected_bpm},
                    actual={"bpm": actual_bpm} if actual_bpm is not None else None,
                    message=None if ok else "Expected tempo was not reflected in final project state.",
                )
            )
            if not ok:
                verification_errors.append(f"step {index} tempo_changed: final tempo does not match expected BPM")

    return verification_results, verification_errors


def get_reaper_client(settings: Settings = Depends(get_settings)) -> ReaperBridgeClient:
    return get_bridge_client(settings)


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    return {
        "ok": True,
        "bridge": {
            "mode": settings.bridge_mode,
            "root": str(settings.bridge_root),
            "poll_interval_ms": settings.bridge_poll_interval_ms,
            "timeout_seconds": settings.bridge_timeout_seconds,
        },
        "planner": {
            "provider": settings.llm_provider,
            "allow_heuristic_fallback": settings.llm_allow_heuristic_fallback,
            "timeout_seconds": settings.llm_timeout_seconds,
        },
    }


@router.get("/state/project")
def project_state(client: ReaperBridgeClient = Depends(get_reaper_client)) -> dict[str, Any]:
    state = client.get_state()
    return {
        "ok": True,
        "mode": client.mode,
        "project": {
            "name": state["project_name"],
            "tracks": state["tracks"],
            "sends": state["sends"],
            "receives": state.get("receives", []),
            "tempo": state["tempo"],
            "markers": state.get("markers", []),
            "regions": state.get("regions", []),
            "selection": state.get("selection", {"tracks": [], "items": []}),
            "selected_track_ids": state.get("selected_track_ids", []),
            "selected_item_count": state.get("selected_item_count", 0),
            "folder_structure": state.get("folder_structure", []),
            "envelopes_summary": state.get("envelopes_summary", []),
            "bridge_connected": state.get("bridge_connected", False),
        },
    }


@router.post("/plan", response_model=PlanResponse)
def plan_endpoint(
    payload: PlanRequest,
    client: ReaperBridgeClient = Depends(get_reaper_client),
) -> PlanResponse:
    _prune_saved_plans(client._settings.saved_plan_ttl_seconds)
    project_state = payload.state or client.get_state()
    try:
        response = plan_prompt_to_actions(
            payload.prompt,
            project_state=project_state,
            clarification_answers=payload.clarification_answers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not response.steps and response.ok:
        return response
    if response.steps:
        plan_id = str(uuid.uuid4())
        _saved_plans[plan_id] = {
            "created_at": time.monotonic(),
            "steps": list(response.steps),
        }
        response = response.model_copy(update={"plan_id": plan_id})
    return response


@router.post("/execute-plan", response_model=ExecutePlanResponse)
def execute_plan(
    payload: ExecutePlanRequest,
    client: ReaperBridgeClient = Depends(get_reaper_client),
) -> ExecutePlanResponse:
    _prune_saved_plans(client._settings.saved_plan_ttl_seconds)
    steps = payload.steps
    if payload.plan_id:
        saved_plan = _saved_plans.get(payload.plan_id)
        if not saved_plan:
            if payload.plan_id in _expired_plan_ids:
                raise HTTPException(status_code=410, detail=f"expired plan_id '{payload.plan_id}'")
            raise HTTPException(status_code=404, detail=f"unknown plan_id '{payload.plan_id}'")
        steps = saved_plan.get("steps")
    if not steps:
        raise HTTPException(status_code=400, detail="plan must include at least one step")
    errors = validate_plan_steps(steps)
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

    try:
        bridge_result = client.execute_plan(steps)
    except ActionExecutionError as exc:
        return ExecutePlanResponse(
            success=False,
            executed_steps=0,
            results=[],
            verification_passed=False,
            verification_results=[],
            verification_errors=[],
            project_state_error=str(exc),
        )

    results: list[StepResult] = []
    for idx, item in enumerate(bridge_result.get("results", [])):
        if not isinstance(item, dict):
            continue
        result_index = int(item.get("index", idx))
        tool = str(item.get("tool", steps[result_index].tool if result_index < len(steps) else "unknown"))
        status = str(item.get("status", "unknown"))
        detail = item.get("output") if "output" in item else item.get("detail")
        results.append(StepResult(index=result_index, tool=tool, status=status, detail=detail))

    failed_step_index = None
    success = bridge_result.get("status") == "ok"
    for item in results:
        if item.status not in {"accepted", "ok"}:
            failed_step_index = item.index
            success = False
            break

    final_project_state = client.get_state()
    verification_results, verification_errors = _verify_steps(steps, results, final_project_state)
    verification_passed = len(verification_errors) == 0
    if not verification_passed:
        success = False

    return ExecutePlanResponse(
        success=bool(success),
        executed_steps=len(results),
        failed_step_index=failed_step_index,
        results=results,
        final_project_state=final_project_state,
        verification_passed=verification_passed,
        verification_results=verification_results,
        verification_errors=verification_errors,
        project_state_error=(
            None
            if success
            else (
                str(bridge_result.get("error"))
                if bridge_result.get("error")
                else ("; ".join(verification_errors) if verification_errors else "bridge execution failed")
            )
        ),
    )
