import pytest
from pydantic import ValidationError

from companion.models.actions import ActionBatch, ReaperAction


def test_valid_actions_batch_passes():
    batch = ActionBatch(
        actions=[
            ReaperAction(type="transport.play", params={}),
            ReaperAction(type="project.set_tempo", params={"bpm": 120}),
            ReaperAction(type="regions.create_song_form", params={}),
        ]
    )
    assert len(batch.actions) == 3


def test_unknown_action_rejected():
    with pytest.raises(ValidationError):
        ReaperAction(type="track.delete", params={})


def test_missing_params_rejected():
    with pytest.raises(ValidationError):
        ReaperAction(type="project.set_tempo", params={})


def test_extra_params_rejected():
    with pytest.raises(ValidationError):
        ReaperAction(type="transport.stop", params={"now": True})


def test_regions_song_form_rejects_params_in_mvp():
    with pytest.raises(ValidationError):
        ReaperAction(type="regions.create_song_form", params={"style": "pop"})


def test_track_actions_validate_params():
    mute = ReaperAction(type="track.mute", params={"track_index": 2, "enabled": True})
    select = ReaperAction(type="track.select", params={"track_index": 1})
    arm = ReaperAction(type="track.record_arm", params={"track_index": 3, "enabled": False})

    assert mute.params["enabled"] is True
    assert select.params["track_index"] == 1
    assert arm.params["enabled"] is False


def test_track_actions_reject_invalid_params():
    with pytest.raises(ValidationError):
        ReaperAction(type="track.select", params={"track_index": 1.0})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.solo", params={"track_index": 0, "enabled": True})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.mute", params={"track_index": 1, "enabled": "yes"})


def test_new_track_and_fx_actions_validate_params():
    create = ReaperAction(type="track.create", params={})
    create_named = ReaperAction(type="track.create", params={"name": "Bass"})
    set_color_index = ReaperAction(type="track.set_color", params={"track_index": 2, "color": "blue"})
    set_color_ref = ReaperAction(
        type="track.set_color", params={"track_ref": "last_created", "color": "green"}
    )
    fx_add_index = ReaperAction(type="fx.add", params={"track_index": 1, "fx_name": "FabFilter Pro-Q 4"})
    fx_add_ref = ReaperAction(
        type="fx.add", params={"track_ref": "last_created", "fx_name": "FabFilter Pro-Q 4"}
    )
    set_volume = ReaperAction(type="track.set_volume", params={"track_index": 8, "db": -5})
    set_pan = ReaperAction(type="track.set_pan", params={"track_index": 3, "pan": -0.25})
    set_name = ReaperAction(type="track.set_name", params={"track_index": 2, "name": "Vox Lead"})
    set_input_audio = ReaperAction(
        type="track.set_input",
        params={"track_index": 1, "input_type": "audio", "input_index": 2, "stereo": False},
    )
    set_input_midi = ReaperAction(
        type="track.set_input",
        params={"track_index": 2, "input_type": "midi", "input_index": 1, "midi_channel": 0},
    )
    set_stereo = ReaperAction(type="track.set_stereo", params={"track_index": 4, "enabled": True})
    set_monitoring = ReaperAction(type="track.set_monitoring", params={"track_index": 4, "enabled": True})
    set_record_mode = ReaperAction(type="track.set_record_mode", params={"track_index": 4, "mode": "midi_overdub"})
    pan_ramp = ReaperAction(
        type="automation.pan_ramp",
        params={
            "track_index": 5,
            "start_time_seconds": 60.0,
            "end_time_seconds": 120.0,
            "start_pan": 0.0,
            "end_pan": -0.5,
        },
    )
    vol_ramp = ReaperAction(
        type="automation.volume_ramp",
        params={
            "track_index": 5,
            "start_time_seconds": 10.0,
            "end_time_seconds": 20.0,
            "start_db": -6.0,
            "end_db": 0.0,
        },
    )
    reaper_action_id = ReaperAction(type="reaper.action", params={"command_id": 40044})
    reaper_action_named = ReaperAction(type="reaper.action", params={"command_name": "_SWS_ABOUT"})

    assert create.params == {}
    assert create_named.params["name"] == "Bass"
    assert set_color_index.params["color"] == "blue"
    assert set_color_ref.params["track_ref"] == "last_created"
    assert fx_add_index.params["track_index"] == 1
    assert fx_add_ref.params["fx_name"] == "FabFilter Pro-Q 4"
    assert set_volume.params["db"] == -5
    assert set_pan.params["pan"] == -0.25
    assert set_name.params["name"] == "Vox Lead"
    assert set_input_audio.params["input_type"] == "audio"
    assert set_input_midi.params["input_type"] == "midi"
    assert set_stereo.params["enabled"] is True
    assert set_monitoring.params["enabled"] is True
    assert set_record_mode.params["mode"] == "midi_overdub"
    assert pan_ramp.params["end_pan"] == -0.5
    assert vol_ramp.params["start_db"] == -6.0
    assert reaper_action_id.params["command_id"] == 40044
    assert reaper_action_named.params["command_name"] == "_SWS_ABOUT"


def test_new_track_and_fx_actions_reject_invalid_params():
    with pytest.raises(ValidationError):
        ReaperAction(type="track.create", params={"name": ""})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_color", params={"color": "blue"})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_color", params={"track_index": 1, "track_ref": "last_created", "color": "blue"})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_color", params={"track_ref": "foo", "color": "blue"})

    with pytest.raises(ValidationError):
        ReaperAction(type="fx.add", params={"track_index": 1})

    with pytest.raises(ValidationError):
        ReaperAction(type="fx.add", params={"track_index": 1, "fx_name": ""})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_volume", params={"track_index": 1})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_volume", params={"track_index": 1, "db": "-5"})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_pan", params={"track_index": 1, "pan": 2})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_name", params={"track_index": 1, "name": ""})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_input", params={"track_index": 1, "input_type": "audio"})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_input", params={"track_index": 1, "input_type": "cv", "input_index": 1})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_stereo", params={"track_index": 1, "enabled": "yes"})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_monitoring", params={"track_index": 1})

    with pytest.raises(ValidationError):
        ReaperAction(type="track.set_record_mode", params={"track_index": 1, "mode": "output"})

    with pytest.raises(ValidationError):
        ReaperAction(
            type="automation.pan_ramp",
            params={
                "track_index": 1,
                "start_time_seconds": 120.0,
                "end_time_seconds": 60.0,
                "start_pan": 0.0,
                "end_pan": 0.5,
            },
        )

    with pytest.raises(ValidationError):
        ReaperAction(
            type="automation.volume_ramp",
            params={
                "track_index": 1,
                "start_time_seconds": 0.0,
                "end_time_seconds": 5.0,
                "start_db": "-6",
                "end_db": 0.0,
            },
        )

    with pytest.raises(ValidationError):
        ReaperAction(type="reaper.action", params={})

    with pytest.raises(ValidationError):
        ReaperAction(type="reaper.action", params={"command_id": 40044, "command_name": "_SWS_ABOUT"})
