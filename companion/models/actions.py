from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ActionType(str, Enum):
    TRANSPORT_PLAY = "transport.play"
    TRANSPORT_STOP = "transport.stop"
    SET_TEMPO = "project.set_tempo"
    ADD_MARKER = "project.add_marker"
    CREATE_SONG_FORM_REGIONS = "regions.create_song_form"
    TRACK_SELECT = "track.select"
    TRACK_MUTE = "track.mute"
    TRACK_SOLO = "track.solo"
    TRACK_RECORD_ARM = "track.record_arm"


class ReaperAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: str = Field(default_factory=lambda: str(uuid4()))

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("request_id must not be empty")
        return value

    @model_validator(mode="after")
    def validate_params_for_type(self) -> "ReaperAction":
        params = self.params
        if self.type in {ActionType.TRANSPORT_PLAY, ActionType.TRANSPORT_STOP}:
            if params:
                raise ValueError(f"{self.type.value} does not accept params")
            return self

        if self.type is ActionType.CREATE_SONG_FORM_REGIONS:
            if params:
                raise ValueError("regions.create_song_form does not accept params in MVP")
            return self

        if self.type is ActionType.SET_TEMPO:
            allowed = {"bpm"}
            if set(params.keys()) != allowed:
                raise ValueError("project.set_tempo requires exactly: bpm")
            bpm = params["bpm"]
            if not isinstance(bpm, (int, float)):
                raise ValueError("bpm must be a number")
            if bpm <= 0:
                raise ValueError("bpm must be > 0")
            return self

        if self.type is ActionType.TRACK_SELECT:
            allowed = {"track_index"}
            if set(params.keys()) != allowed:
                raise ValueError("track.select requires exactly: track_index")
            track_index = params["track_index"]
            if not isinstance(track_index, int):
                raise ValueError("track_index must be an integer")
            if track_index <= 0:
                raise ValueError("track_index must be >= 1")
            return self

        if self.type in {
            ActionType.TRACK_MUTE,
            ActionType.TRACK_SOLO,
            ActionType.TRACK_RECORD_ARM,
        }:
            allowed = {"track_index", "enabled"}
            if set(params.keys()) != allowed:
                raise ValueError(
                    f"{self.type.value} requires exactly: track_index, enabled"
                )
            track_index = params["track_index"]
            enabled = params["enabled"]
            if not isinstance(track_index, int):
                raise ValueError("track_index must be an integer")
            if track_index <= 0:
                raise ValueError("track_index must be >= 1")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            return self

        if self.type is ActionType.ADD_MARKER:
            allowed = {"position_seconds", "name"}
            if set(params.keys()) != allowed:
                raise ValueError("project.add_marker requires exactly: position_seconds, name")
            position = params["position_seconds"]
            name = params["name"]
            if not isinstance(position, (int, float)):
                raise ValueError("position_seconds must be a number")
            if position < 0:
                raise ValueError("position_seconds must be >= 0")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("name must be a non-empty string")
            return self

        raise ValueError(f"unsupported action type: {self.type}")


class ActionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[ReaperAction] = Field(min_length=1, max_length=50)
