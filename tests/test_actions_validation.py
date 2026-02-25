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
