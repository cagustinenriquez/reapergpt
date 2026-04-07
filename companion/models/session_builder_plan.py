from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


class PlanStep(BaseModel):
    id: str
    title: str
    description: str | None = None


class EntityRef(BaseModel):
    track_id: int | None = None
    name: str | None = None
    action_id: str | None = None

    @model_validator(mode="after")
    def validate_selector(self) -> "EntityRef":
        selected = [self.track_id is not None, bool(self.name), bool(self.action_id)]
        if sum(selected) != 1:
            raise ValueError("entity refs must set exactly one of track_id, name, or action_id")
        return self


class TrackCreateAction(BaseModel):
    id: str
    action: Literal["track.create"]
    name: str = ""  # empty = let REAPER auto-name


class TrackRenameAction(BaseModel):
    id: str
    action: Literal["track.rename"]
    target: EntityRef
    name: str


class TrackColorAction(BaseModel):
    id: str
    action: Literal["track.color"]
    target: EntityRef
    color: str


class TrackPanAction(BaseModel):
    id: str
    action: Literal["track.pan"]
    target: EntityRef
    pan: float


class BusCreateAction(BaseModel):
    id: str
    action: Literal["bus.create"]
    name: str = ""  # empty = let REAPER auto-name


class SendCreateAction(BaseModel):
    id: str
    action: Literal["send.create"]
    source: EntityRef
    destination: EntityRef
    mode: Literal["post-fader", "pre-fader"] = "post-fader"


class FxInsertAction(BaseModel):
    id: str
    action: Literal["fx.insert"]
    target: EntityRef
    fx_name: str


class TempoSetAction(BaseModel):
    id: str
    action: Literal["project.set_tempo"]
    bpm: float


class TransportAction(BaseModel):
    id: str
    action: Literal["transport.play", "transport.stop"]


SessionAction = Annotated[
    TrackCreateAction
    | TrackRenameAction
    | TrackColorAction
    | TrackPanAction
    | BusCreateAction
    | SendCreateAction
    | FxInsertAction
    | TempoSetAction
    | TransportAction,
    Field(discriminator="action"),
]


class SessionBuilderPlan(BaseModel):
    version: Literal["0.2"] = "0.2"
    summary: str
    steps: list[PlanStep] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    actions: list[SessionAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_actions(self) -> "SessionBuilderPlan":
        action_ids = [action.id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action ids must be unique")
        return self


class PromptRequest(BaseModel):
    prompt: str


class PromptResponse(BaseModel):
    ok: bool = True
    plan_id: str | None = None
    plan: SessionBuilderPlan
