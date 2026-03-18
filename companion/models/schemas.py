from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class PlanRequest(BaseModel):
    prompt: str
    state: dict[str, Any] = Field(default_factory=dict)


class PlanResponse(BaseModel):
    ok: bool = True
    summary: str = ''
    source: str = 'heuristic'
    requires_confirmation: bool = False
    steps: list[PlanStep] = Field(default_factory=list)


class ExecutePlanRequest(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)


class StepResult(BaseModel):
    index: int
    tool: str
    status: str
    detail: dict[str, Any] | str | None = None


class ExecutePlanResponse(BaseModel):
    success: bool
    results: list[StepResult] = Field(default_factory=list)
    executed_steps: int = 0
    failed_step_index: int | None = None
    final_project_state: dict[str, Any] | None = None
    project_state_error: str | None = None
