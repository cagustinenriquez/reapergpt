from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class PlanStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class PlanRequest(BaseModel):
    prompt: str
    state: dict[str, Any] = Field(default_factory=dict)
    clarification_answers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def coerce_empty_collections(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        if isinstance(payload.get("state"), list) and len(payload["state"]) == 0:
            payload["state"] = {}
        if isinstance(payload.get("clarification_answers"), list) and len(payload["clarification_answers"]) == 0:
            payload["clarification_answers"] = {}
        return payload


class ClarificationOption(BaseModel):
    value: str
    label: str


class ClarificationPrompt(BaseModel):
    id: str
    question: str
    options: list[ClarificationOption] = Field(default_factory=list)


class PlanResponse(BaseModel):
    ok: bool = True
    summary: str = ''
    source: str = 'heuristic'
    requires_confirmation: bool = False
    requires_clarification: bool = False
    clarification: ClarificationPrompt | None = None
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


class VerificationResult(BaseModel):
    index: int
    tool: str
    check: str
    ok: bool
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] | None = None
    message: str | None = None


class ExecutePlanResponse(BaseModel):
    success: bool
    results: list[StepResult] = Field(default_factory=list)
    executed_steps: int = 0
    failed_step_index: int | None = None
    final_project_state: dict[str, Any] | None = None
    verification_passed: bool = False
    verification_results: list[VerificationResult] = Field(default_factory=list)
    verification_errors: list[str] = Field(default_factory=list)
    project_state_error: str | None = None
