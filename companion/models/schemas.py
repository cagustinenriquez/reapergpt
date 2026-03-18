from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


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
    plan_id: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)


class ExecutePlanRequest(BaseModel):
    steps: list[PlanStep] | None = None
    plan_id: str | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> "ExecutePlanRequest":
        has_steps = bool(self.steps)
        has_plan_id = bool(self.plan_id)
        if has_steps == has_plan_id:
            raise ValueError("provide exactly one of steps or plan_id")
        return self


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
