import json
import threading
import time
from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient

from companion.api.routes import get_settings
from companion.config import Settings, reset_settings
from companion.daws.reaper.client import reset_bridge_client
from companion.main import app


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bridge_settings(test_name: str) -> Settings:
    bridge_root = Path("data") / "pytest_bridge" / f"{test_name}_{uuid.uuid4().hex}"
    return Settings(
        bridge_root=bridge_root,
        bridge_timeout_seconds=1.5,
        bridge_poll_interval_ms=25,
        saved_plan_ttl_seconds=300.0,
    )


def _simulate_reaper(settings: Settings, state_payload: dict, result_builder) -> threading.Thread:
    def worker() -> None:
        deadline = time.monotonic() + settings.bridge_timeout_seconds
        while time.monotonic() < deadline:
            request_path = settings.bridge_request_path
            if not request_path.exists():
                time.sleep(settings.bridge_poll_interval_ms / 1000.0)
                continue
            request = json.loads(request_path.read_text(encoding="utf-8"))
            _write_json(settings.bridge_state_path, state_payload)
            _write_json(settings.bridge_result_path, result_builder(request))
            request_path.unlink(missing_ok=True)
            return

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


@pytest.fixture(autouse=True)
def _reset_globals():
    reset_bridge_client()
    reset_settings()
    app.dependency_overrides.clear()
    yield
    reset_bridge_client()
    reset_settings()
    app.dependency_overrides.clear()


@pytest.fixture
def bridge_cleanup():
    created_paths: list[Path] = []

    def register(path: Path) -> Path:
        created_paths.append(path)
        return path

    yield register

    for path in reversed(created_paths):
        if not path.exists():
            continue
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        path.rmdir()


def test_health_endpoint_returns_bridge_and_planner_info(bridge_cleanup):
    settings = _bridge_settings("health")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["bridge"]["mode"] == "file"
    assert body["planner"]["provider"] == "heuristic"


def test_plan_endpoint_builds_drum_bus_plan_from_bridge_state(bridge_cleanup):
    settings = _bridge_settings("plan_state")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Demo",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Kick", "fx": [], "color": None},
                {"id": 2, "name": "Snare", "fx": [], "color": None},
            ],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "Create a drum bus and route Kick and Snare to it."})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["source"] == "heuristic"
    assert body["plan_id"] is not None
    assert body["steps"][0]["tool"] == "create_bus"
    assert any(step["tool"] == "create_send" for step in body["steps"])


def test_plan_endpoint_builds_track_and_bus_creation_prompt(bridge_cleanup):
    settings = _bridge_settings("plan_create_track_bus")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "create a track called Voice and a bus called Voice Bus"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert [step["tool"] for step in body["steps"]] == ["create_track", "create_bus"]
    assert body["steps"][0]["args"]["name"] == "Voice"
    assert body["steps"][1]["args"]["name"] == "Voice Bus"


def test_plan_endpoint_builds_generic_create_track_prompt(bridge_cleanup):
    settings = _bridge_settings("plan_create_track_generic")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "create track"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["steps"] == [{"tool": "create_track", "args": {}}]


def test_plan_endpoint_builds_generic_create_bus_prompt(bridge_cleanup):
    settings = _bridge_settings("plan_create_bus_generic")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "create bus"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["steps"] == [{"tool": "create_bus", "args": {}}]


def test_plan_endpoint_builds_routing_prompt_from_existing_state(bridge_cleanup):
    settings = _bridge_settings("plan_route_existing")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Routing Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Voice", "fx": [], "color": None},
                {"id": 2, "name": "Voice Bus", "fx": [], "color": None},
            ],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "send Voice to Voice Bus pre-fader"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["steps"][0]["tool"] == "create_send"
    assert body["steps"][0]["args"]["src"] == {"type": "track_id", "value": 1}
    assert body["steps"][0]["args"]["dst"] == {"type": "track_id", "value": 2}
    assert body["steps"][0]["args"]["pre_fader"] is True


def test_plan_endpoint_builds_fx_insert_prompt(bridge_cleanup):
    settings = _bridge_settings("plan_fx_insert")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "FX Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Vocal", "fx": [], "color": None},
            ],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "put compressor on vocal"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["steps"] == [
        {
            "tool": "insert_fx",
            "args": {
                "track_ref": {"type": "track_id", "value": 1},
                "fx_name": "ReaComp",
            },
        }
    ]


def test_plan_endpoint_returns_clarification_for_ambiguous_fx_target(bridge_cleanup):
    settings = _bridge_settings("plan_clarify_fx")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Clarify Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Guitar L", "fx": [], "color": None},
                {"id": 2, "name": "Guitar R", "fx": [], "color": None},
            ],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "add EQ to guitar"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["requires_clarification"] is True
    assert body["clarification"]["id"] == "fx_target_track"
    assert "Which one should I use?" in body["clarification"]["question"]
    assert [item["label"] for item in body["clarification"]["options"]] == ["Guitar L", "Guitar R"]
    assert body["steps"] == []


def test_plan_endpoint_uses_clarification_answer_for_ambiguous_fx_target(bridge_cleanup):
    settings = _bridge_settings("plan_clarify_fx_answered")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Clarify Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Guitar L", "fx": [], "color": None},
                {"id": 2, "name": "Guitar R", "fx": [], "color": None},
            ],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post(
        "/plan",
        json={
            "prompt": "add EQ to guitar",
            "clarification_answers": {"fx_target_track": "Guitar R"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["requires_clarification"] is False
    assert body["steps"] == [
        {
            "tool": "insert_fx",
            "args": {
                "track_ref": {"type": "track_name", "value": "Guitar R"},
                "fx_name": "ReaEQ",
            },
        }
    ]


def test_plan_endpoint_builds_fx_insert_for_unique_partial_match(bridge_cleanup):
    settings = _bridge_settings("plan_unique_partial_fx")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Unique FX Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Bass Guitar", "fx": [], "color": None},
            ],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "add eq to guitar"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["requires_clarification"] is False
    assert body["steps"] == [
        {
            "tool": "insert_fx",
            "args": {
                "track_ref": {"type": "track_id", "value": 1},
                "fx_name": "ReaEQ",
            },
        }
    ]


def test_plan_endpoint_returns_clarification_for_missing_fx_target(bridge_cleanup):
    settings = _bridge_settings("plan_missing_fx_target")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Missing FX Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Piano", "fx": [], "color": None},
                {"id": 2, "name": "Bass", "fx": [], "color": None},
            ],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "add eq to guitar"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["requires_clarification"] is True
    assert body["clarification"]["id"] == "fx_target_track"
    assert "couldn't find a track matching 'Guitar'" in body["clarification"]["question"]
    assert [item["label"] for item in body["clarification"]["options"]] == ["Piano", "Bass"]
    assert body["steps"] == []


def test_plan_endpoint_builds_basic_vocal_setup_prompt(bridge_cleanup):
    settings = _bridge_settings("plan_vocal_setup")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": "create a vocal track and a vocal bus, then route it"})

    assert response.status_code == 200
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert [step["tool"] for step in body["steps"]] == [
        "create_track",
        "create_bus",
        "create_send",
        "insert_fx",
        "insert_fx",
    ]
    assert body["steps"][0]["args"]["name"] == "Vocal"
    assert body["steps"][1]["args"]["name"] == "Vocal Bus"
    assert body["steps"][2]["args"]["src"] == {"type": "track_name", "value": "Vocal"}
    assert body["steps"][2]["args"]["dst"] == {"type": "track_name", "value": "Vocal Bus"}


def test_plan_endpoint_returns_400_for_empty_prompt(bridge_cleanup):
    settings = _bridge_settings("empty_prompt")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/plan", json={"prompt": ""})

    assert response.status_code == 400
    assert "prompt must not be empty" in response.json()["detail"]


def test_project_state_snapshot_reads_bridge_file(bridge_cleanup):
    settings = _bridge_settings("project_state")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Bridge Session",
            "tempo": 98.0,
            "tracks": [
                {
                    "id": 1,
                    "name": "Bass",
                    "fx": ["VST: ReaEQ (Cockos)"],
                    "fx_count": 1,
                    "color": None,
                    "selected": True,
                    "sends": [{"index": 0, "src": 1, "dst": 2, "dst_name": "Bass Bus"}],
                    "receives": [],
                    "depth": 0,
                    "parent_track_id": None,
                    "folder_depth_delta": 1,
                    "is_folder_parent": True,
                    "has_parent_send": True,
                    "is_bus": False,
                },
                {
                    "id": 2,
                    "name": "Bass Bus",
                    "fx": [],
                    "fx_count": 0,
                    "color": None,
                    "selected": False,
                    "sends": [],
                    "receives": [{"index": 0, "src": 1, "src_name": "Bass", "dst": 2}],
                    "depth": 1,
                    "parent_track_id": 1,
                    "folder_depth_delta": -1,
                    "is_folder_parent": False,
                    "has_parent_send": True,
                    "is_bus": True,
                },
            ],
            "sends": [{"src": 1, "dst": 2}],
            "receives": [{"src": 1, "dst": 2}],
            "markers": [{"id": 1, "name": "Verse", "start": 4.0}],
            "regions": [{"id": 2, "name": "Chorus", "start": 8.0, "end": 16.0}],
            "selected_track_ids": [1],
            "selected_item_count": 1,
            "folder_structure": [
                {"id": 1, "name": "Bass", "parent_track_id": None, "depth": 0, "is_folder_parent": True, "is_bus": False},
                {"id": 2, "name": "Bass Bus", "parent_track_id": 1, "depth": 1, "is_folder_parent": False, "is_bus": True},
            ],
            "selection": {
                "tracks": [{"id": 1, "name": "Bass"}],
                "items": [{"index": 1, "position": 1.0, "length": 2.0, "track_id": 1, "track_name": "Bass", "take_name": "Bass DI"}],
            },
            "envelopes_summary": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.get("/state/project")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["mode"] == "file"
    assert body["project"]["name"] == "Bridge Session"
    assert body["project"]["tempo"] == 98.0
    assert body["project"]["tracks"][0]["name"] == "Bass"
    assert body["project"]["tracks"][0]["fx"] == ["VST: ReaEQ (Cockos)"]
    assert body["project"]["tracks"][0]["selected"] is True
    assert body["project"]["tracks"][1]["receives"][0]["src_name"] == "Bass"
    assert body["project"]["selection"]["tracks"] == [{"id": 1, "name": "Bass"}]
    assert body["project"]["selection"]["items"][0]["take_name"] == "Bass DI"
    assert body["project"]["selected_track_ids"] == [1]
    assert body["project"]["selected_item_count"] == 1
    assert body["project"]["markers"] == [{"id": 1, "name": "Verse", "start": 4.0}]
    assert body["project"]["regions"] == [{"id": 2, "name": "Chorus", "start": 8.0, "end": 16.0}]
    assert body["project"]["folder_structure"][1]["parent_track_id"] == 1
    assert body["project"]["bridge_connected"] is True


def test_execute_plan_round_trips_through_file_bridge(bridge_cleanup):
    settings = _bridge_settings("execute_success")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Executed Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Demo Track", "fx": [], "color": None, "selected": False, "sends": [{"index": 0, "src": 1, "dst": 2, "dst_name": "Demo Bus", "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 2, "name": "Demo Bus", "fx": [], "color": None, "selected": False, "sends": [], "receives": [{"index": 0, "src": 1, "src_name": "Demo Track", "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": True},
            ],
            "sends": [{"src": 1, "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}],
            "receives": [{"src": 1, "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}],
            "markers": [],
            "regions": [],
            "selected_track_ids": [],
            "selected_item_count": 0,
            "folder_structure": [],
            "selection": {"tracks": [], "items": []},
            "envelopes_summary": [],
            "bridge_connected": True,
        },
        result_builder=lambda request: {
            "request_id": request["request_id"],
            "status": "ok",
            "results": [
                {"index": 0, "tool": "create_track", "status": "accepted", "output": {"track_id": 1}},
                {"index": 1, "tool": "create_bus", "status": "accepted", "output": {"track_id": 2}},
                {"index": 2, "tool": "create_send", "status": "accepted", "output": {"send_index": 0}},
            ],
        },
    )

    response = client.post(
        "/execute-plan",
        json={
            "steps": [
                {"tool": "create_track", "args": {"name": "Demo Track"}},
                {"tool": "create_bus", "args": {"name": "Demo Bus"}},
                {
                    "tool": "create_send",
                    "args": {
                        "src": {"type": "track_name", "value": "Demo Track"},
                        "dst": {"type": "track_name", "value": "Demo Bus"},
                    },
                },
            ]
        },
    )
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["executed_steps"] == 3
    assert body["failed_step_index"] is None
    assert body["verification_passed"] is True
    assert [item["check"] for item in body["verification_results"]] == [
        "track_created",
        "bus_created",
        "send_exists",
    ]
    assert body["verification_errors"] == []
    assert body["final_project_state"]["tracks"][0]["name"] == "Demo Track"
    assert body["final_project_state"]["tracks"][1]["is_bus"] is True


def test_execute_plan_combined_scenario_round_trips_through_file_bridge(bridge_cleanup):
    settings = _bridge_settings("execute_combined")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Combined Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Demo Track", "fx": ["VST: ReaEQ (Cockos)"], "color": None, "selected": True, "sends": [{"index": 0, "src": 1, "dst": 2, "dst_name": "Demo Bus", "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 2, "name": "Demo Bus", "fx": [], "color": None, "selected": False, "sends": [], "receives": [{"index": 0, "src": 1, "src_name": "Demo Track", "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": True},
            ],
            "sends": [{"src": 1, "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}],
            "receives": [{"src": 1, "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}],
            "markers": [{"id": 1, "name": "Intro", "start": 0.0}],
            "regions": [],
            "selected_track_ids": [1],
            "selected_item_count": 0,
            "folder_structure": [{"id": 1, "name": "Demo Track", "parent_track_id": None, "depth": 0, "is_folder_parent": False, "is_bus": False}],
            "selection": {"tracks": [{"id": 1, "name": "Demo Track"}], "items": []},
            "envelopes_summary": [],
            "bridge_connected": True,
        },
        result_builder=lambda request: {
            "request_id": request["request_id"],
            "status": "ok",
            "results": [
                {"index": 0, "tool": "create_track", "status": "accepted", "output": {"track_id": 1}},
                {"index": 1, "tool": "create_bus", "status": "accepted", "output": {"track_id": 2}},
                {"index": 2, "tool": "create_send", "status": "accepted", "output": {"send_index": 0}},
                {"index": 3, "tool": "insert_fx", "status": "accepted", "output": {"fx_index": 0, "fx_name": "ReaEQ"}},
            ],
        },
    )

    response = client.post(
        "/execute-plan",
        json={
            "steps": [
                {"tool": "create_track", "args": {"name": "Demo Track"}},
                {"tool": "create_bus", "args": {"name": "Demo Bus"}},
                {
                    "tool": "create_send",
                    "args": {
                        "src": {"type": "track_name", "value": "Demo Track"},
                        "dst": {"type": "track_name", "value": "Demo Bus"},
                    },
                },
                {
                    "tool": "insert_fx",
                    "args": {
                        "track_ref": {"type": "track_name", "value": "Demo Track"},
                        "fx_name": "ReaEQ",
                    },
                },
            ]
        },
    )
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["executed_steps"] == 4
    assert body["verification_passed"] is True
    assert [item["tool"] for item in body["results"]] == [
        "create_track",
        "create_bus",
        "create_send",
        "insert_fx",
    ]
    assert [item["check"] for item in body["verification_results"]] == [
        "track_created",
        "bus_created",
        "send_exists",
        "fx_inserted",
    ]
    assert body["final_project_state"]["sends"] == [{"src": 1, "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}]
    assert body["final_project_state"]["receives"] == [{"src": 1, "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}]
    assert body["final_project_state"]["tracks"][0]["fx"] == ["VST: ReaEQ (Cockos)"]
    assert body["final_project_state"]["selected_track_ids"] == [1]


def test_plan_id_preview_can_be_executed_later(bridge_cleanup):
    settings = _bridge_settings("plan_id")
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Plan Session",
            "tempo": 120.0,
            "tracks": [],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    preview = client.post("/plan", json={"prompt": "tempo 132"})
    assert preview.status_code == 200
    preview_body = preview.json()
    assert preview_body["plan_id"] is not None
    assert preview_body["steps"][0]["tool"] == "project.set_tempo"

    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Plan Session",
            "tempo": 132.0,
            "tracks": [],
            "sends": [],
            "bridge_connected": True,
        },
        result_builder=lambda request: {
            "request_id": request["request_id"],
            "status": "ok",
            "results": [
                {"index": 0, "tool": "project.set_tempo", "status": "accepted", "output": {"tempo": 132}},
            ],
        },
    )

    response = client.post("/execute-plan", json={"plan_id": preview_body["plan_id"]})
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["results"][0]["tool"] == "project.set_tempo"
    assert body["final_project_state"]["tempo"] == 132.0
    assert body["verification_passed"] is True
    assert body["verification_results"][0]["check"] == "tempo_changed"


def test_execute_plan_returns_410_for_expired_plan_id(bridge_cleanup):
    settings = Settings(
        bridge_root=Path("data") / "pytest_bridge" / f"expired_plan_{uuid.uuid4().hex}",
        bridge_timeout_seconds=1.5,
        bridge_poll_interval_ms=25,
        saved_plan_ttl_seconds=0.01,
    )
    bridge_cleanup(settings.bridge_root)
    _write_json(
        settings.bridge_state_path,
        {
            "project_name": "Expired Plan Session",
            "tempo": 120.0,
            "tracks": [],
            "sends": [],
            "bridge_connected": True,
        },
    )
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    preview = client.post("/plan", json={"prompt": "tempo 132"})
    assert preview.status_code == 200
    plan_id = preview.json()["plan_id"]
    time.sleep(0.05)

    response = client.post("/execute-plan", json={"plan_id": plan_id})

    assert response.status_code == 410
    assert f"expired plan_id '{plan_id}'" in response.json()["detail"]


def test_execute_plan_returns_404_for_unknown_plan_id(bridge_cleanup):
    settings = _bridge_settings("unknown_plan_id")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/execute-plan", json={"plan_id": "missing-plan-id"})

    assert response.status_code == 404
    assert "unknown plan_id 'missing-plan-id'" in response.json()["detail"]


def test_execute_plan_returns_bridge_failure_details(bridge_cleanup):
    settings = _bridge_settings("execute_failure")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Failed Session",
            "tempo": 120.0,
            "tracks": [],
            "sends": [],
            "bridge_connected": True,
        },
        result_builder=lambda request: {
            "request_id": request["request_id"],
            "status": "error",
            "error": "track not found",
            "results": [
                {"index": 0, "tool": "create_send", "status": "rejected", "detail": "track not found"},
            ],
        },
    )

    response = client.post(
        "/execute-plan",
        json={
            "steps": [
                {
                    "tool": "create_send",
                    "args": {
                        "src": {"type": "track_name", "value": "Missing"},
                        "dst": {"type": "track_name", "value": "Bus"},
                    },
                }
            ]
        },
    )
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["failed_step_index"] == 0
    assert body["results"][0]["status"] == "rejected"


def test_execute_plan_reports_verification_mismatch_when_final_state_does_not_match(bridge_cleanup):
    settings = _bridge_settings("execute_verification_mismatch")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Mismatch Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Demo Track", "fx": [], "color": None, "selected": False, "sends": [], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
            ],
            "sends": [],
            "receives": [],
            "markers": [],
            "regions": [],
            "selected_track_ids": [],
            "selected_item_count": 0,
            "folder_structure": [],
            "selection": {"tracks": [], "items": []},
            "envelopes_summary": [],
            "bridge_connected": True,
        },
        result_builder=lambda request: {
            "request_id": request["request_id"],
            "status": "ok",
            "results": [
                {"index": 0, "tool": "create_track", "status": "accepted", "output": {"track_id": 1}},
                {"index": 1, "tool": "insert_fx", "status": "accepted", "output": {"fx_index": 0, "fx_name": "ReaEQ"}},
            ],
        },
    )

    response = client.post(
        "/execute-plan",
        json={
            "steps": [
                {"tool": "create_track", "args": {"name": "Demo Track"}},
                {
                    "tool": "insert_fx",
                    "args": {
                        "track_ref": {"type": "track_name", "value": "Demo Track"},
                        "fx_name": "ReaEQ",
                    },
                },
            ]
        },
    )
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["verification_passed"] is False
    assert body["verification_results"][0]["ok"] is True
    assert body["verification_results"][1]["check"] == "fx_inserted"
    assert body["verification_results"][1]["ok"] is False
    assert "fx_inserted" in body["verification_errors"][0]


def test_execute_plan_verifies_pre_fader_send_mode(bridge_cleanup):
    settings = _bridge_settings("execute_pre_fader")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Pre Fader Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Voice", "fx": [], "color": None, "selected": False, "sends": [{"index": 0, "src": 1, "dst": 2, "dst_name": "Voice Bus", "send_mode": 1, "send_mode_name": "pre-fx", "pre_fader": True}], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 2, "name": "Voice Bus", "fx": [], "color": None, "selected": False, "sends": [], "receives": [{"index": 0, "src": 1, "src_name": "Voice", "dst": 2, "send_mode": 1, "send_mode_name": "pre-fx", "pre_fader": True}], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": True},
            ],
            "sends": [{"src": 1, "dst": 2, "send_mode": 1, "send_mode_name": "pre-fx", "pre_fader": True}],
            "receives": [{"src": 1, "dst": 2, "send_mode": 1, "send_mode_name": "pre-fx", "pre_fader": True}],
            "markers": [],
            "regions": [],
            "selected_track_ids": [],
            "selected_item_count": 0,
            "folder_structure": [],
            "selection": {"tracks": [], "items": []},
            "envelopes_summary": [],
            "bridge_connected": True,
        },
        result_builder=lambda request: {
            "request_id": request["request_id"],
            "status": "ok",
            "results": [
                {"index": 0, "tool": "create_send", "status": "accepted", "output": {"send_index": 0, "send_mode": 1, "pre_fader": True}},
            ],
        },
    )

    response = client.post(
        "/execute-plan",
        json={
            "steps": [
                {
                    "tool": "create_send",
                    "args": {
                        "src": {"type": "track_name", "value": "Voice"},
                        "dst": {"type": "track_name", "value": "Voice Bus"},
                        "pre_fader": True,
                    },
                }
            ]
        },
    )
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["verification_passed"] is True
    assert body["verification_results"][0]["check"] == "send_exists"
    assert body["verification_results"][0]["expected"]["pre_fader"] is True
    assert body["final_project_state"]["sends"][0]["pre_fader"] is True


def test_execute_plan_reports_send_mode_mismatch(bridge_cleanup):
    settings = _bridge_settings("execute_send_mode_mismatch")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Send Mode Mismatch",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Voice", "fx": [], "color": None, "selected": False, "sends": [{"index": 0, "src": 1, "dst": 2, "dst_name": "Voice Bus", "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 2, "name": "Voice Bus", "fx": [], "color": None, "selected": False, "sends": [], "receives": [{"index": 0, "src": 1, "src_name": "Voice", "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": True},
            ],
            "sends": [{"src": 1, "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}],
            "receives": [{"src": 1, "dst": 2, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}],
            "markers": [],
            "regions": [],
            "selected_track_ids": [],
            "selected_item_count": 0,
            "folder_structure": [],
            "selection": {"tracks": [], "items": []},
            "envelopes_summary": [],
            "bridge_connected": True,
        },
        result_builder=lambda request: {
            "request_id": request["request_id"],
            "status": "ok",
            "results": [
                {"index": 0, "tool": "create_send", "status": "accepted", "output": {"send_index": 0, "send_mode": 0, "pre_fader": False}},
            ],
        },
    )

    response = client.post(
        "/execute-plan",
        json={
            "steps": [
                {
                    "tool": "create_send",
                    "args": {
                        "src": {"type": "track_name", "value": "Voice"},
                        "dst": {"type": "track_name", "value": "Voice Bus"},
                        "pre_fader": True,
                    },
                }
            ]
        },
    )
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["verification_passed"] is False
    assert body["verification_results"][0]["check"] == "send_exists"
    assert body["verification_results"][0]["ok"] is False
    assert "send mode mismatch" in body["verification_errors"][0]
