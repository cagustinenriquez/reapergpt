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
    assert body["steps"][0]["tool"] == "create_bus"
    assert any(step["tool"] == "create_send" for step in body["steps"])


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
            "tracks": [{"id": 1, "name": "Bass", "fx": [], "color": None}],
            "sends": [{"src": 1, "dst": 2}],
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
    assert body["project"]["tempo"] == 98.0
    assert body["project"]["tracks"][0]["name"] == "Bass"


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
                {"id": 1, "name": "Demo Track", "fx": [], "color": None},
                {"id": 2, "name": "Demo Bus", "fx": [], "color": None},
            ],
            "sends": [{"src": 1, "dst": 2}],
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
    assert body["final_project_state"]["tracks"][0]["name"] == "Demo Track"


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
