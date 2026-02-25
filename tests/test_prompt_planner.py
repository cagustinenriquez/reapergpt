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
