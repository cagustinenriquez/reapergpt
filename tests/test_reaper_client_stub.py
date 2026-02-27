from pathlib import Path

from companion.daws.reaper.client import ReaperBridgeClient
from companion.models.actions import ActionBatch, ReaperAction


def test_reaper_client_stub_accepts_actions():
    client = ReaperBridgeClient(base_url="http://127.0.0.1:8765", dry_run=True)
    batch = ActionBatch(actions=[ReaperAction(type="transport.play", params={})])

    result = client.send_actions(batch)

    assert result["mode"] == "dry_run"
    assert len(result["results"]) == 1
    assert result["results"][0]["status"] == "accepted"


def test_reaper_client_stub_returns_project_state_shape():
    client = ReaperBridgeClient(base_url="http://127.0.0.1:8765", dry_run=True)

    result = client.get_project_state()

    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert "project" in result
    assert result["project"]["selection"]["selected_item_count"] == 0


def test_file_bridge_parser_ignores_wrong_batch():
    parsed = ReaperBridgeClient._parse_file_bridge_response(
        "batch_id=other\nreq-1\taccepted\tok\n",
        batch_id="expected",
    )
    assert parsed is None


def test_file_bridge_rejects_unsupported_actions_without_io(tmp_path: Path):
    client = ReaperBridgeClient(
        base_url="http://127.0.0.1:8765",
        dry_run=False,
        transport="file",
        bridge_dir=str(tmp_path),
    )
    batch = ActionBatch(
        actions=[
            ReaperAction(
                type="project.add_marker",
                params={"position_seconds": 1.0, "name": "A"},
            )
        ]
    )

    result = client.send_actions(batch)

    assert result["mode"] == "file_bridge"
    assert result["results"][0]["status"] == "rejected"
    assert "track.record_arm" in result["results"][0]["detail"]


def test_file_bridge_writes_params_for_supported_actions(tmp_path: Path):
    client = ReaperBridgeClient(
        base_url="http://127.0.0.1:8765",
        dry_run=False,
        transport="file",
        bridge_dir=str(tmp_path),
        timeout_seconds=0.01,
    )
    batch = ActionBatch(
        actions=[
            ReaperAction(type="project.set_tempo", params={"bpm": 128}),
            ReaperAction(type="track.solo", params={"track_index": 2, "enabled": True}),
        ]
    )

    result = client.send_actions(batch)

    assert result["mode"] == "file_bridge"
    assert len(result["results"]) == 2
    assert all(item["status"] == "rejected" for item in result["results"])

    command_text = (tmp_path / "commands.txt").read_text(encoding="utf-8")
    assert '"bpm":128' in command_text
    assert '"track_index":2' in command_text
    assert '"enabled":true' in command_text


def test_file_bridge_reads_project_state_snapshot(tmp_path: Path):
    client = ReaperBridgeClient(
        base_url="http://127.0.0.1:8765",
        dry_run=False,
        transport="file",
        bridge_dir=str(tmp_path),
    )
    (tmp_path / "project_state.json").write_text(
        (
            '{"ok":true,"mode":"file_bridge","project":{"project_name":"Song","project_path":"",'
            '"tempo_bpm":128.0,"play_state":"stopped","tracks":[{"index":1,"name":"Guitar","selected":true,'
            '"muted":false,"solo":false,"record_armed":false,"volume_db":0.0,"pan":0.0,"fx_chain":["ReaEQ"],'
            '"sends":[],"receives":[]}],"markers":[],"regions":[],"selection":{"selected_track_index":1,'
            '"selected_item_count":0,"selected_region_index":null},"envelopes_summary":{"volume_envelopes":0,'
            '"pan_envelopes":0,"other_envelopes":0}}}'
        ),
        encoding="utf-8",
    )

    result = client.get_project_state()

    assert result["ok"] is True
    assert result["mode"] == "file_bridge"
    assert result["project"]["tracks"][0]["name"] == "Guitar"
