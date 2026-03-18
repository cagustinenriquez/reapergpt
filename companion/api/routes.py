from __future__ import annotations

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
    StepResult,
)

router = APIRouter()
_saved_plans: dict[str, list[PlanStep]] = {}


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
            "tracks": state["tracks"],
            "sends": state["sends"],
            "tempo": state["tempo"],
            "markers": [],
            "regions": [],
            "selection": {"tracks": [], "items": []},
            "envelopes_summary": [],
        },
    }


@router.post("/plan", response_model=PlanResponse)
def plan_endpoint(
    payload: PlanRequest,
    client: ReaperBridgeClient = Depends(get_reaper_client),
) -> PlanResponse:
    project_state = payload.state or client.get_state()
    try:
        response = plan_prompt_to_actions(payload.prompt, project_state=project_state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not response.steps and response.ok:
        return PlanResponse(
            ok=False,
            summary=response.summary,
            source=response.source,
            requires_confirmation=response.requires_confirmation,
            steps=[],
        )
    if response.steps:
        plan_id = str(uuid.uuid4())
        _saved_plans[plan_id] = list(response.steps)
        response = response.model_copy(update={"plan_id": plan_id})
    return response


@router.post("/execute-plan", response_model=ExecutePlanResponse)
def execute_plan(
    payload: ExecutePlanRequest,
    client: ReaperBridgeClient = Depends(get_reaper_client),
) -> ExecutePlanResponse:
    steps = payload.steps
    if payload.plan_id:
        steps = _saved_plans.get(payload.plan_id)
        if not steps:
            raise HTTPException(status_code=404, detail=f"unknown plan_id '{payload.plan_id}'")
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

    return ExecutePlanResponse(
        success=bool(success),
        executed_steps=len(results),
        failed_step_index=failed_step_index,
        results=results,
        final_project_state=client.get_state(),
        project_state_error=None if success else str(bridge_result.get("error") or "bridge execution failed"),
    )
