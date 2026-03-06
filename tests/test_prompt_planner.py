from companion.config import Settings
from companion.llm.planner import _extract_json_object, plan_prompt_to_actions


def test_planner_maps_pop_regions_heuristically():
    settings = Settings()
    result = plan_prompt_to_actions("create regions for a pop song", settings)

    assert result.source == "heuristic"
    assert result.batch is not None
    assert result.batch.actions[0].type.value == "regions.create_song_form"


def test_planner_maps_tempo_heuristically():
    settings = Settings()
    result = plan_prompt_to_actions("tempo 128", settings)

    assert result.batch is not None
    assert result.batch.actions[0].type.value == "project.set_tempo"
    assert result.batch.actions[0].params["bpm"] == 128.0


def test_planner_returns_unsupported_for_unknown_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("write me a full symphony", settings)

    assert result.source == "unsupported"
    assert result.batch is None


def test_planner_maps_natural_tempo_phrase_heuristically():
    settings = Settings()
    result = plan_prompt_to_actions("please set the tempo to 128 bpm", settings)

    assert result.batch is not None
    assert result.source == "heuristic"
    assert result.batch.actions[0].type.value == "project.set_tempo"
    assert result.batch.actions[0].params["bpm"] == 128.0


def test_planner_maps_transport_phrases_heuristically():
    settings = Settings()
    result = plan_prompt_to_actions("start transport", settings)

    assert result.batch is not None
    assert result.batch.actions[0].type.value == "transport.play"

    result = plan_prompt_to_actions("pause playback", settings)
    assert result.batch is not None
    assert result.batch.actions[0].type.value == "transport.stop"


def test_planner_supports_multi_action_prompt_heuristically():
    settings = Settings()
    result = plan_prompt_to_actions("set tempo 128 and play", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == [
        "project.set_tempo",
        "transport.play",
    ]


def test_extract_json_object_handles_fenced_json_with_nested_braces():
    text = """```json
{"actions":[{"type":"project.set_tempo","params":{"bpm":128}}]}
```"""

    obj = _extract_json_object(text)
    assert obj is not None
    assert obj["actions"][0]["params"]["bpm"] == 128


def test_planner_maps_track_control_prompts():
    settings = Settings()

    solo = plan_prompt_to_actions("solo track 2", settings)
    assert solo.batch is not None
    assert solo.batch.actions[0].type.value == "track.solo"
    assert solo.batch.actions[0].params == {"track_index": 2, "enabled": True}

    unmute = plan_prompt_to_actions("unmute track 4", settings)
    assert unmute.batch is not None
    assert unmute.batch.actions[0].type.value == "track.mute"
    assert unmute.batch.actions[0].params == {"track_index": 4, "enabled": False}

    select = plan_prompt_to_actions("select track 1", settings)
    assert select.batch is not None
    assert select.batch.actions[0].type.value == "track.select"
    assert select.batch.actions[0].params == {"track_index": 1}

    arm = plan_prompt_to_actions("record arm track 3", settings)
    assert arm.batch is not None
    assert arm.batch.actions[0].type.value == "track.record_arm"
    assert arm.batch.actions[0].params == {"track_index": 3, "enabled": True}


def test_planner_supports_multi_action_track_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("select track 2 and solo track 2", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.select", "track.solo"]


def test_planner_maps_track_create_color_and_fx_chain():
    settings = Settings()
    result = plan_prompt_to_actions(
        "create a new track colored blue and add fabfilter q4 to it",
        settings,
    )

    assert result.source == "heuristic"
    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == [
        "track.create",
        "track.set_color",
        "fx.add",
    ]
    assert result.batch.actions[1].params == {"color": "blue", "track_ref": "last_created"}
    assert result.batch.actions[2].params == {
        "fx_name": "FabFilter Pro-Q 4",
        "track_ref": "last_created",
    }


def test_planner_maps_fx_add_to_specific_track():
    settings = Settings()
    result = plan_prompt_to_actions("add fabfilter q4 to track 1", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["fx.add"]
    assert result.batch.actions[0].params == {"fx_name": "FabFilter Pro-Q 4", "track_index": 1}


def test_planner_maps_track_color_for_specific_track():
    settings = Settings()
    result = plan_prompt_to_actions("color track 2 blue", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_color"]
    assert result.batch.actions[0].params == {"color": "blue", "track_index": 2}


def test_planner_maps_track_color_with_track_first_word_order():
    settings = Settings()
    result = plan_prompt_to_actions("track 1 color blue", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_color"]
    assert result.batch.actions[0].params == {"color": "blue", "track_index": 1}


def test_planner_maps_track_volume_db_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("set volume to -5 db on track 8", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_volume"]
    assert result.batch.actions[0].params == {"track_index": 8, "db": -5.0}


def test_planner_maps_track_pan_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("set pan to 25% left on track 3", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_pan"]
    assert result.batch.actions[0].params == {"track_index": 3, "pan": -0.25}


def test_planner_maps_track_rename_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("rename track 2 to Vox Lead", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_name"]
    assert result.batch.actions[0].params == {"track_index": 2, "name": "Vox Lead"}


def test_planner_maps_generic_reaper_action_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("run action 40044", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["reaper.action"]
    assert result.batch.actions[0].params == {"command_id": 40044}


def test_planner_maps_track_input_midi_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("set the input of track 4 to midi #1", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_input"]
    assert result.batch.actions[0].params == {
        "track_index": 4,
        "input_type": "midi",
        "input_index": 1,
        "midi_channel": 0,
    }


def test_planner_maps_track_input_audio_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("input #2 on track 5", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_input"]
    assert result.batch.actions[0].params == {
        "track_index": 5,
        "input_type": "audio",
        "input_index": 2,
        "stereo": False,
    }


def test_planner_maps_make_track_stereo_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("make track 6 stereo", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_stereo"]
    assert result.batch.actions[0].params == {"track_index": 6, "enabled": True}


def test_planner_maps_pan_automation_time_range_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("set automation for pan for track 2 from 1:00 to 2:00", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["automation.pan_ramp"]
    assert result.batch.actions[0].params == {
        "track_index": 2,
        "start_time_seconds": 60.0,
        "end_time_seconds": 120.0,
        "start_pan": 0.0,
        "end_pan": 0.0,
    }


def test_planner_maps_track_monitoring_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("enable input monitoring on track 7", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_monitoring"]
    assert result.batch.actions[0].params == {"track_index": 7, "enabled": True}


def test_planner_maps_track_record_mode_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("set record mode to midi overdub on track 3", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.set_record_mode"]
    assert result.batch.actions[0].params == {"track_index": 3, "mode": "midi_overdub"}


def test_planner_maps_volume_automation_time_range_prompt():
    settings = Settings()
    result = plan_prompt_to_actions(
        "set automation for volume for track 4 from 1:00 to 2:00 start volume -6 db end volume 0 db",
        settings,
    )

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["automation.volume_ramp"]
    assert result.batch.actions[0].params == {
        "track_index": 4,
        "start_time_seconds": 60.0,
        "end_time_seconds": 120.0,
        "start_db": -6.0,
        "end_db": 0.0,
    }


def test_planner_maps_render_selected_region_mp3_to_desktop_prompt():
    settings = Settings()
    result = plan_prompt_to_actions(
        "render to mp3 128kbps for the selected region to my desktop",
        settings,
    )

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["project.render_region"]
    assert result.batch.actions[0].params == {
        "region_scope": "selected",
        "format": "mp3",
        "mp3_bitrate_kbps": 128,
        "output_dir": "desktop",
    }


def test_planner_maps_create_send_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("create send from track 8 to track 9", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.create_send"]
    assert result.batch.actions[0].params == {"source_track_index": 8, "dest_track_index": 9}


def test_planner_maps_create_receive_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("create receive from track 7 to 8", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.create_receive"]
    assert result.batch.actions[0].params == {"source_track_index": 7, "dest_track_index": 8}


def test_planner_maps_create_track_short_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("create track", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.create"]
    assert result.batch.actions[0].params == {}


def test_planner_maps_create_track_named_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("create track named Guitar", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.create"]
    assert result.batch.actions[0].params == {"name": "Guitar"}


def test_planner_maps_add_track_called_prompt():
    settings = Settings()
    result = plan_prompt_to_actions('add track called "Vox Lead"', settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.create"]
    assert result.batch.actions[0].params == {"name": "Vox Lead"}


def test_planner_maps_make_track_short_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("make track", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.create"]
    assert result.batch.actions[0].params == {}


def test_planner_maps_delete_track_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("delete track 1", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.delete"]
    assert result.batch.actions[0].params == {"track_index": 1}


def test_planner_maps_remove_track_prompt():
    settings = Settings()
    result = plan_prompt_to_actions("remove track 2", settings)

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.delete"]
    assert result.batch.actions[0].params == {"track_index": 2}


def test_planner_uses_profile_defaults_for_track_create():
    settings = Settings()
    result = plan_prompt_to_actions(
        "create track",
        settings,
        preferences={"default_track_color": "orange", "track_naming_prefix": "Lead"},
    )

    assert result.batch is not None
    assert [a.type.value for a in result.batch.actions] == ["track.create", "track.set_color"]
    assert result.batch.actions[0].params == {"name": "Lead 1"}
    assert result.batch.actions[1].params == {"color": "orange", "track_ref": "last_created"}
