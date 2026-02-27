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
    PROJECT_RENDER_REGION = "project.render_region"
    CREATE_SONG_FORM_REGIONS = "regions.create_song_form"
    TRACK_CREATE = "track.create"
    TRACK_DELETE = "track.delete"
    TRACK_SELECT = "track.select"
    TRACK_SET_NAME = "track.set_name"
    TRACK_SET_COLOR = "track.set_color"
    TRACK_SET_VOLUME = "track.set_volume"
    TRACK_SET_PAN = "track.set_pan"
    TRACK_SET_INPUT = "track.set_input"
    TRACK_SET_STEREO = "track.set_stereo"
    TRACK_SET_MONITORING = "track.set_monitoring"
    TRACK_SET_RECORD_MODE = "track.set_record_mode"
    TRACK_CREATE_SEND = "track.create_send"
    TRACK_CREATE_RECEIVE = "track.create_receive"
    TRACK_MUTE = "track.mute"
    TRACK_SOLO = "track.solo"
    TRACK_RECORD_ARM = "track.record_arm"
    FX_ADD = "fx.add"
    AUTOMATION_PAN_RAMP = "automation.pan_ramp"
    AUTOMATION_VOLUME_RAMP = "automation.volume_ramp"
    REAPER_ACTION = "reaper.action"


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

        if self.type is ActionType.PROJECT_RENDER_REGION:
            allowed = {"region_scope", "format", "mp3_bitrate_kbps", "output_dir"}
            if set(params.keys()) != allowed:
                raise ValueError(
                    "project.render_region requires exactly: region_scope, format, mp3_bitrate_kbps, output_dir"
                )
            if params["region_scope"] not in {"selected"}:
                raise ValueError("region_scope must be 'selected'")
            if params["format"] not in {"mp3"}:
                raise ValueError("format must be 'mp3' in current MVP")
            bitrate = params["mp3_bitrate_kbps"]
            if not isinstance(bitrate, int) or bitrate <= 0:
                raise ValueError("mp3_bitrate_kbps must be an integer > 0")
            if params["output_dir"] not in {"desktop"}:
                raise ValueError("output_dir must be 'desktop' in current MVP")
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

        if self.type is ActionType.TRACK_CREATE:
            allowed = {"name"}
            if not set(params.keys()).issubset(allowed):
                raise ValueError("track.create accepts only: name (optional)")
            if "name" in params and (not isinstance(params["name"], str) or not params["name"].strip()):
                raise ValueError("name must be a non-empty string")
            return self

        if self.type is ActionType.TRACK_DELETE:
            allowed = {"track_index"}
            if set(params.keys()) != allowed:
                raise ValueError("track.delete requires exactly: track_index")
            track_index = params["track_index"]
            if not isinstance(track_index, int):
                raise ValueError("track_index must be an integer")
            if track_index <= 0:
                raise ValueError("track_index must be >= 1")
            return self

        if self.type in {ActionType.TRACK_SET_COLOR, ActionType.FX_ADD}:
            value_key = "color" if self.type is ActionType.TRACK_SET_COLOR else "fx_name"
            if value_key not in params:
                raise ValueError(f"{self.type.value} requires: {value_key}")
            target_keys = {"track_index", "track_ref"} & set(params.keys())
            if len(target_keys) != 1:
                raise ValueError(f"{self.type.value} requires exactly one of: track_index or track_ref")

            allowed = {value_key, "track_index", "track_ref"}
            if set(params.keys()) != ({value_key} | target_keys):
                raise ValueError(
                    f"{self.type.value} accepts only target ({'/'.join(sorted(target_keys))}) and {value_key}"
                )

            if "track_index" in params:
                track_index = params["track_index"]
                if not isinstance(track_index, int):
                    raise ValueError("track_index must be an integer")
                if track_index <= 0:
                    raise ValueError("track_index must be >= 1")
            if "track_ref" in params:
                track_ref = params["track_ref"]
                if track_ref != "last_created":
                    raise ValueError("track_ref must be 'last_created'")

            value = params[value_key]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{value_key} must be a non-empty string")
            return self

        if self.type is ActionType.TRACK_SET_VOLUME:
            allowed = {"track_index", "db"}
            if set(params.keys()) != allowed:
                raise ValueError("track.set_volume requires exactly: track_index, db")
            track_index = params["track_index"]
            db = params["db"]
            if not isinstance(track_index, int):
                raise ValueError("track_index must be an integer")
            if track_index <= 0:
                raise ValueError("track_index must be >= 1")
            if not isinstance(db, (int, float)):
                raise ValueError("db must be a number")
            return self

        if self.type is ActionType.TRACK_SET_PAN:
            allowed = {"track_index", "pan"}
            if set(params.keys()) != allowed:
                raise ValueError("track.set_pan requires exactly: track_index, pan")
            track_index = params["track_index"]
            pan = params["pan"]
            if not isinstance(track_index, int):
                raise ValueError("track_index must be an integer")
            if track_index <= 0:
                raise ValueError("track_index must be >= 1")
            if not isinstance(pan, (int, float)):
                raise ValueError("pan must be a number")
            if pan < -1 or pan > 1:
                raise ValueError("pan must be between -1 and 1")
            return self

        if self.type is ActionType.TRACK_SET_NAME:
            allowed = {"track_index", "name"}
            if set(params.keys()) != allowed:
                raise ValueError("track.set_name requires exactly: track_index, name")
            track_index = params["track_index"]
            name = params["name"]
            if not isinstance(track_index, int):
                raise ValueError("track_index must be an integer")
            if track_index <= 0:
                raise ValueError("track_index must be >= 1")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("name must be a non-empty string")
            return self

        if self.type is ActionType.TRACK_SET_INPUT:
            allowed = {"track_index", "input_type", "input_index", "stereo", "midi_channel"}
            if not set(params.keys()).issubset(allowed):
                raise ValueError(
                    "track.set_input accepts only: track_index, input_type, input_index, stereo, midi_channel"
                )
            required = {"track_index", "input_type", "input_index"}
            if not required.issubset(params.keys()):
                raise ValueError("track.set_input requires: track_index, input_type, input_index")
            track_index = params["track_index"]
            input_type = params["input_type"]
            input_index = params["input_index"]
            if not isinstance(track_index, int) or track_index <= 0:
                raise ValueError("track_index must be an integer >= 1")
            if input_type not in {"audio", "midi"}:
                raise ValueError("input_type must be 'audio' or 'midi'")
            if not isinstance(input_index, int) or input_index <= 0:
                raise ValueError("input_index must be an integer >= 1")
            if "stereo" in params and not isinstance(params["stereo"], bool):
                raise ValueError("stereo must be a boolean")
            if "midi_channel" in params:
                ch = params["midi_channel"]
                if not isinstance(ch, int):
                    raise ValueError("midi_channel must be an integer")
                if ch < 0 or ch > 16:
                    raise ValueError("midi_channel must be between 0 and 16")
            return self

        if self.type is ActionType.TRACK_SET_STEREO:
            allowed = {"track_index", "enabled"}
            if set(params.keys()) != allowed:
                raise ValueError("track.set_stereo requires exactly: track_index, enabled")
            track_index = params["track_index"]
            enabled = params["enabled"]
            if not isinstance(track_index, int) or track_index <= 0:
                raise ValueError("track_index must be an integer >= 1")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            return self

        if self.type is ActionType.TRACK_SET_MONITORING:
            allowed = {"track_index", "enabled"}
            if set(params.keys()) != allowed:
                raise ValueError("track.set_monitoring requires exactly: track_index, enabled")
            track_index = params["track_index"]
            enabled = params["enabled"]
            if not isinstance(track_index, int) or track_index <= 0:
                raise ValueError("track_index must be an integer >= 1")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            return self

        if self.type is ActionType.TRACK_SET_RECORD_MODE:
            allowed = {"track_index", "mode"}
            if set(params.keys()) != allowed:
                raise ValueError("track.set_record_mode requires exactly: track_index, mode")
            track_index = params["track_index"]
            mode = params["mode"]
            if not isinstance(track_index, int) or track_index <= 0:
                raise ValueError("track_index must be an integer >= 1")
            if mode not in {"input", "midi_overdub", "midi_replace"}:
                raise ValueError("mode must be one of: input, midi_overdub, midi_replace")
            return self

        if self.type in {ActionType.TRACK_CREATE_SEND, ActionType.TRACK_CREATE_RECEIVE}:
            allowed = {"source_track_index", "dest_track_index"}
            if set(params.keys()) != allowed:
                raise ValueError(f"{self.type.value} requires exactly: source_track_index, dest_track_index")
            src = params["source_track_index"]
            dst = params["dest_track_index"]
            if not isinstance(src, int) or src <= 0:
                raise ValueError("source_track_index must be an integer >= 1")
            if not isinstance(dst, int) or dst <= 0:
                raise ValueError("dest_track_index must be an integer >= 1")
            if src == dst:
                raise ValueError("source_track_index and dest_track_index must be different")
            return self

        if self.type is ActionType.AUTOMATION_PAN_RAMP:
            allowed = {"track_index", "start_time_seconds", "end_time_seconds", "start_pan", "end_pan"}
            if set(params.keys()) != allowed:
                raise ValueError(
                    "automation.pan_ramp requires exactly: track_index, start_time_seconds, end_time_seconds, start_pan, end_pan"
                )
            track_index = params["track_index"]
            start_t = params["start_time_seconds"]
            end_t = params["end_time_seconds"]
            start_pan = params["start_pan"]
            end_pan = params["end_pan"]
            if not isinstance(track_index, int) or track_index <= 0:
                raise ValueError("track_index must be an integer >= 1")
            for key, value in [("start_time_seconds", start_t), ("end_time_seconds", end_t)]:
                if not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be a number")
                if value < 0:
                    raise ValueError(f"{key} must be >= 0")
            if end_t <= start_t:
                raise ValueError("end_time_seconds must be > start_time_seconds")
            for key, value in [("start_pan", start_pan), ("end_pan", end_pan)]:
                if not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be a number")
                if value < -1 or value > 1:
                    raise ValueError(f"{key} must be between -1 and 1")
            return self

        if self.type is ActionType.AUTOMATION_VOLUME_RAMP:
            allowed = {"track_index", "start_time_seconds", "end_time_seconds", "start_db", "end_db"}
            if set(params.keys()) != allowed:
                raise ValueError(
                    "automation.volume_ramp requires exactly: track_index, start_time_seconds, end_time_seconds, start_db, end_db"
                )
            track_index = params["track_index"]
            start_t = params["start_time_seconds"]
            end_t = params["end_time_seconds"]
            start_db = params["start_db"]
            end_db = params["end_db"]
            if not isinstance(track_index, int) or track_index <= 0:
                raise ValueError("track_index must be an integer >= 1")
            for key, value in [("start_time_seconds", start_t), ("end_time_seconds", end_t)]:
                if not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be a number")
                if value < 0:
                    raise ValueError(f"{key} must be >= 0")
            if end_t <= start_t:
                raise ValueError("end_time_seconds must be > start_time_seconds")
            for key, value in [("start_db", start_db), ("end_db", end_db)]:
                if not isinstance(value, (int, float)):
                    raise ValueError(f"{key} must be a number")
            return self

        if self.type is ActionType.REAPER_ACTION:
            target_keys = {"command_id", "command_name"} & set(params.keys())
            if len(target_keys) != 1:
                raise ValueError("reaper.action requires exactly one of: command_id or command_name")
            allowed = {"command_id", "command_name", "section_id"}
            if not set(params.keys()).issubset(allowed):
                raise ValueError("reaper.action accepts only: command_id or command_name, and optional section_id")
            if "command_id" in params:
                command_id = params["command_id"]
                if not isinstance(command_id, int):
                    raise ValueError("command_id must be an integer")
                if command_id <= 0:
                    raise ValueError("command_id must be >= 1")
            if "command_name" in params:
                command_name = params["command_name"]
                if not isinstance(command_name, str) or not command_name.strip():
                    raise ValueError("command_name must be a non-empty string")
            if "section_id" in params:
                section_id = params["section_id"]
                if not isinstance(section_id, int):
                    raise ValueError("section_id must be an integer")
                if section_id < 0:
                    raise ValueError("section_id must be >= 0")
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
