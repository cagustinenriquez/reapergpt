from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from companion.models.actions import ActionBatch, ReaperAction


class SubmitActionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch: ActionBatch


class ActionDispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: Literal["accepted", "rejected"]
    detail: str | None = None


class SubmitActionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    mode: Literal["dry_run", "http_bridge", "file_bridge"]
    planner_source: str | None = None
    results: list[ActionDispatchResult] = Field(default_factory=list)


class PromptDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2000)


class PromptClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    question: str


class PromptClarificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    needs_clarification: Literal[True] = True
    session_id: str
    message: str
    questions: list[PromptClarificationQuestion] = Field(default_factory=list)


class PromptContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    answers: dict[str, Any] = Field(default_factory=dict)


class UserProfilePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_plugins: dict[str, list[str]] = Field(default_factory=dict)
    default_sound_style: str = "classic rock"
    track_naming_prefix: str = ""
    default_track_color: str = ""
    routing_template_default: str = ""
    include_fx_by_default: bool = True


class UserProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_plugins: dict[str, list[str]] | None = None
    default_sound_style: str | None = Field(default=None, min_length=1, max_length=80)
    track_naming_prefix: str | None = Field(default=None, min_length=1, max_length=80)
    default_track_color: str | None = Field(default=None, min_length=1, max_length=40)
    routing_template_default: str | None = Field(default=None, min_length=1, max_length=80)
    include_fx_by_default: bool | None = None


class UserProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    profile: UserProfilePreferences


class ProjectStateTrackRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_index: int = Field(ge=1)


class ProjectStateTrackSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    name: str = ""
    selected: bool = False
    muted: bool = False
    solo: bool = False
    record_armed: bool = False
    volume_db: float | None = None
    pan: float | None = None
    fx_chain: list[str] = Field(default_factory=list)
    sends: list[ProjectStateTrackRoute] = Field(default_factory=list)
    receives: list[ProjectStateTrackRoute] = Field(default_factory=list)


class ProjectStateMarkerSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    name: str = ""
    position_seconds: float = 0.0


class ProjectStateRegionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    name: str = ""
    start_seconds: float = 0.0
    end_seconds: float = 0.0
    selected: bool = False


class ProjectStateSelectionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_track_index: int | None = Field(default=None, ge=1)
    selected_item_count: int = Field(default=0, ge=0)
    selected_region_index: int | None = Field(default=None, ge=0)


class ProjectStateEnvelopeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    volume_envelopes: int = Field(default=0, ge=0)
    pan_envelopes: int = Field(default=0, ge=0)
    other_envelopes: int = Field(default=0, ge=0)


class ProjectStateSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str = ""
    project_path: str = ""
    tempo_bpm: float | None = None
    play_state: str = "unknown"
    tracks: list[ProjectStateTrackSnapshot] = Field(default_factory=list)
    markers: list[ProjectStateMarkerSnapshot] = Field(default_factory=list)
    regions: list[ProjectStateRegionSnapshot] = Field(default_factory=list)
    selection: ProjectStateSelectionSnapshot = Field(default_factory=ProjectStateSelectionSnapshot)
    envelopes_summary: ProjectStateEnvelopeSummary = Field(default_factory=ProjectStateEnvelopeSummary)


class ProjectStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    mode: Literal["dry_run", "http_bridge", "file_bridge"]
    project: ProjectStateSnapshot


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=2000)
    include_project_state: bool = True


class PlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    source: str
    proposed_actions: list[ReaperAction] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    project_state_included: bool = False
    plan_id: str | None = None


class ExecutePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str | None = Field(default=None, min_length=1)
    actions: list[ReaperAction] | None = Field(default=None)
    stop_on_failure: bool = True

    @model_validator(mode="after")
    def validate_plan_source(self) -> "ExecutePlanRequest":
        has_plan_id = bool((self.plan_id or "").strip())
        has_actions = bool(self.actions)
        if has_plan_id == has_actions:
            raise ValueError("Provide exactly one of: plan_id or actions")
        if self.actions is not None and not (1 <= len(self.actions) <= 50):
            raise ValueError("actions must contain between 1 and 50 items")
        return self


class ExecutePlanStepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(ge=1)
    action: ReaperAction
    status: Literal["accepted", "rejected"]
    detail: str | None = None


class ExecutePlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    mode: Literal["dry_run", "http_bridge", "file_bridge"]
    stop_on_failure: bool
    total_steps: int = Field(ge=1)
    executed_steps: int = Field(ge=0)
    failed_step_index: int | None = Field(default=None, ge=1)
    results: list[ExecutePlanStepResult] = Field(default_factory=list)
    final_project_state: ProjectStateSnapshot | None = None
    project_state_error: str | None = None
