import json
import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from companion.api.routes import get_settings
from companion.config import Settings, reset_settings
from companion.daws.reaper.client import reset_bridge_client
from companion.main import app
from companion.models.session_builder_plan import SessionBuilderPlan

ROUTING_PROMPT = "create Lead Vocal, Double L, Double R, Vocal Bus, and route all vocals to the bus"


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


def test_session_builder_plan_validates_valid_plan():
    plan = SessionBuilderPlan.model_validate(
        {
            "summary": "Create a vocal route.",
            "steps": [{"id": "step_1", "title": "Create track"}],
            "warnings": [],
            "requires_confirmation": True,
            "actions": [
                {"id": "track_1", "action": "track.create", "name": "Lead Vocal"},
                {"id": "bus_1", "action": "bus.create", "name": "Vocal Bus"},
                {
                    "id": "send_1",
                    "action": "send.create",
                    "source": {"action_id": "track_1"},
                    "destination": {"action_id": "bus_1"},
                },
            ],
        }
    )

    assert plan.version == "0.2"
    assert len(plan.actions) == 3


def test_session_builder_plan_rejects_unknown_action():
    with pytest.raises(ValidationError) as exc:
        SessionBuilderPlan.model_validate(
            {
                "summary": "Invalid plan.",
                "actions": [
                    {"id": "bad_1", "action": "track.delete", "name": "Lead Vocal"},
                ],
            }
        )

    assert "track.delete" in str(exc.value)


def test_session_builder_plan_rejects_missing_required_fields():
    with pytest.raises(ValidationError) as exc:
        SessionBuilderPlan.model_validate(
            {
                "summary": "Invalid send.",
                "actions": [
                    {
                        "id": "send_1",
                        "action": "send.create",
                        "source": {"name": "Lead Vocal"},
                    }
                ],
            }
        )

    assert "destination" in str(exc.value)


def test_session_builder_plan_rejects_ambiguous_refs():
    with pytest.raises(ValidationError) as exc:
        SessionBuilderPlan.model_validate(
            {
                "summary": "Ambiguous reference.",
                "actions": [
                    {
                        "id": "send_1",
                        "action": "send.create",
                        "source": {"name": "Lead Vocal", "action_id": "track_1"},
                        "destination": {"name": "Vocal Bus"},
                    }
                ],
            }
        )

    assert "exactly one of track_id, name, or action_id" in str(exc.value)


def test_prompt_endpoint_builds_single_track_plan(bridge_cleanup):
    settings = _bridge_settings("session_builder_prompt")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/prompt", json={"prompt": "Create a track named Lead Vocal."})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["plan_id"] is not None
    assert body["plan"]["summary"] == "Create Lead Vocal."
    assert [step["title"] for step in body["plan"]["steps"]] == ["Create track"]
    assert [action["action"] for action in body["plan"]["actions"]] == ["track.create"]
    assert body["plan"]["actions"][0]["name"] == "Lead Vocal"


def test_prompt_endpoint_builds_tracks_bus_and_routes_only_when_requested(bridge_cleanup):
    settings = _bridge_settings("session_builder_prompt_routing")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post(
        "/prompt",
        json={"prompt": ROUTING_PROMPT},
    )

    assert response.status_code == 200
    body = response.json()
    assert [action["action"] for action in body["plan"]["actions"]] == [
        "track.create",
        "track.create",
        "track.create",
        "bus.create",
        "send.create",
        "send.create",
        "send.create",
    ]
    assert body["plan"]["actions"][4]["source"]["action_id"] == "track_lead_vocal"
    assert body["plan"]["actions"][4]["destination"]["action_id"] == "bus_vocal_bus"


def test_prompt_endpoint_builds_track_and_bus_without_over_generation(bridge_cleanup):
    settings = _bridge_settings("session_builder_prompt_track_bus")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post(
        "/prompt",
        json={"prompt": "Create a track named Lead Vocal and a bus named Vocal Bus."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["plan"]["summary"] == "Create Lead Vocal; create Vocal Bus."
    assert [action["action"] for action in body["plan"]["actions"]] == ["track.create", "bus.create"]
    assert [action["name"] for action in body["plan"]["actions"]] == ["Lead Vocal", "Vocal Bus"]


def test_prompt_endpoint_builds_explicit_route_plan_with_created_refs(bridge_cleanup):
    settings = _bridge_settings("session_builder_prompt_explicit_route")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post(
        "/prompt",
        json={"prompt": "Create a track named Lead Vocal and a bus named Vocal Bus and route Lead Vocal to Vocal Bus."},
    )

    assert response.status_code == 200
    body = response.json()
    assert [action["action"] for action in body["plan"]["actions"]] == ["track.create", "bus.create", "send.create"]
    assert body["plan"]["actions"][2]["source"] == {"track_id": None, "name": None, "action_id": "track_lead_vocal"}
    assert body["plan"]["actions"][2]["destination"] == {"track_id": None, "name": None, "action_id": "bus_vocal_bus"}


def test_prompt_endpoint_builds_route_only_plan_with_named_refs(bridge_cleanup):
    settings = _bridge_settings("session_builder_prompt_route_only")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    response = client.post("/prompt", json={"prompt": "Route Lead Vocal to Vocal Bus."})

    assert response.status_code == 200
    body = response.json()
    assert [action["action"] for action in body["plan"]["actions"]] == ["send.create"]
    assert body["plan"]["actions"][0]["source"] == {"track_id": None, "name": "Lead Vocal", "action_id": None}
    assert body["plan"]["actions"][0]["destination"] == {"track_id": None, "name": "Vocal Bus", "action_id": None}


def test_prompt_plan_id_executes_compiled_actions(bridge_cleanup):
    settings = _bridge_settings("session_builder_execute")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    preview = client.post("/prompt", json={"prompt": ROUTING_PROMPT})
    assert preview.status_code == 200
    plan_id = preview.json()["plan_id"]

    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Prompt Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Lead Vocal", "fx": [], "color": None, "selected": False, "sends": [{"index": 0, "src": 1, "dst": 4, "dst_name": "Vocal Bus", "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 2, "name": "Double L", "fx": [], "color": None, "selected": False, "sends": [{"index": 0, "src": 2, "dst": 4, "dst_name": "Vocal Bus", "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 3, "name": "Double R", "fx": [], "color": None, "selected": False, "sends": [{"index": 0, "src": 3, "dst": 4, "dst_name": "Vocal Bus", "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 4, "name": "Vocal Bus", "fx": [], "color": None, "selected": False, "sends": [], "receives": [{"index": 0, "src": 1, "src_name": "Lead Vocal", "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}, {"index": 1, "src": 2, "src_name": "Double L", "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}, {"index": 2, "src": 3, "src_name": "Double R", "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": True},
            ],
            "sends": [{"src": 1, "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}, {"src": 2, "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}, {"src": 3, "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}],
            "receives": [{"src": 1, "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}, {"src": 2, "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}, {"src": 3, "dst": 4, "send_mode": 0, "send_mode_name": "post-fader", "pre_fader": False}],
            "markers": [],
            "regions": [],
            "selected_track_ids": [],
            "selected_item_count": 0,
            "folder_structure": [],
            "selection": {"tracks": [], "items": []},
            "envelopes_summary": [],
            "bridge_connected": True,
        },
        result_builder=lambda request: _typed_action_success_result(request),
    )

    response = client.post("/execute-plan", json={"plan_id": plan_id})
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert [item["tool"] for item in body["results"]] == ["track.create", "track.create", "track.create", "bus.create", "send.create", "send.create", "send.create"]
    assert [item["action_id"] for item in body["results"]] == ["track_lead_vocal", "track_double_l", "track_double_r", "bus_vocal_bus", "send_lead_vocal_to_vocal_bus", "send_double_l_to_vocal_bus", "send_double_r_to_vocal_bus"]
    assert body["verification_passed"] is True
    assert [item["check"] for item in body["verification_results"]] == ["track_created", "track_created", "track_created", "bus_created", "send_exists", "send_exists", "send_exists"]
    assert [item["action_id"] for item in body["verification_results"]] == ["track_lead_vocal", "track_double_l", "track_double_r", "bus_vocal_bus", "send_lead_vocal_to_vocal_bus", "send_double_l_to_vocal_bus", "send_double_r_to_vocal_bus"]


def test_prompt_plan_id_writes_typed_actions_to_bridge_request(bridge_cleanup):
    settings = _bridge_settings("session_builder_request_shape")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    preview = client.post("/prompt", json={"prompt": ROUTING_PROMPT})
    assert preview.status_code == 200
    plan_id = preview.json()["plan_id"]

    observed_request: dict | None = None

    def result_builder(request: dict) -> dict:
        nonlocal observed_request
        observed_request = request
        return _typed_action_success_result(request)

    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Prompt Session",
            "tempo": 120.0,
            "tracks": [],
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
        result_builder=result_builder,
    )

    response = client.post("/execute-plan", json={"plan_id": plan_id})
    worker.join(timeout=2.0)

    assert response.status_code == 200
    assert observed_request is not None
    assert "actions" in observed_request
    assert "steps" not in observed_request
    assert [action["action"] for action in observed_request["actions"]] == ["track.create", "track.create", "track.create", "bus.create", "send.create", "send.create", "send.create"]


def test_prompt_plan_id_returns_structured_action_failure(bridge_cleanup):
    settings = _bridge_settings("session_builder_action_failure")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    preview = client.post("/prompt", json={"prompt": ROUTING_PROMPT})
    assert preview.status_code == 200
    plan_id = preview.json()["plan_id"]

    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Prompt Session",
            "tempo": 120.0,
            "tracks": [],
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
            "status": "error",
            "error": "unknown action reference",
            "results": [
                {
                    "index": 2,
                    "action": "send.create",
                    "action_id": "send_lead_vocal_to_vocal_bus",
                    "status": "rejected",
                    "detail": {
                        "action_id": "send_lead_vocal_to_vocal_bus",
                        "error": "unknown action reference",
                    },
                }
            ],
        },
    )

    response = client.post("/execute-plan", json={"plan_id": plan_id})
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["results"][0]["tool"] == "send.create"
    assert body["results"][0]["action_id"] == "send_lead_vocal_to_vocal_bus"
    assert body["results"][0]["detail"]["error"] == "unknown action reference"
    assert body["project_state_error"] == "action send_lead_vocal_to_vocal_bus (send.create) failed: unknown action reference"


def test_prompt_plan_id_surfaces_verification_failure_with_action_id(bridge_cleanup):
    settings = _bridge_settings("session_builder_verification_failure")
    bridge_cleanup(settings.bridge_root)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)

    preview = client.post("/prompt", json={"prompt": ROUTING_PROMPT})
    assert preview.status_code == 200
    plan_id = preview.json()["plan_id"]

    worker = _simulate_reaper(
        settings,
        state_payload={
            "project_name": "Prompt Session",
            "tempo": 120.0,
            "tracks": [
                {"id": 1, "name": "Lead Vocal", "fx": [], "color": None, "selected": False, "sends": [], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 2, "name": "Double L", "fx": [], "color": None, "selected": False, "sends": [], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 3, "name": "Double R", "fx": [], "color": None, "selected": False, "sends": [], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
                {"id": 4, "name": "Vocal Bus", "fx": [], "color": None, "selected": False, "sends": [], "receives": [], "depth": 0, "parent_track_id": None, "folder_depth_delta": 0, "is_folder_parent": False, "has_parent_send": True, "is_bus": False},
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
        result_builder=lambda request: _typed_action_success_result(request),
    )

    response = client.post("/execute-plan", json={"plan_id": plan_id})
    worker.join(timeout=2.0)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["verification_passed"] is False
    assert body["verification_results"][4]["action_id"] == "send_lead_vocal_to_vocal_bus"
    assert "action send_lead_vocal_to_vocal_bus send_exists" in body["verification_errors"][0]


def _typed_action_success_result(request: dict) -> dict:
    assert "actions" in request
    assert [action["action"] for action in request["actions"]] == ["track.create", "track.create", "track.create", "bus.create", "send.create", "send.create", "send.create"]
    return {
        "request_id": request["request_id"],
        "status": "ok",
        "results": [
            {"index": 0, "action": "track.create", "action_id": "track_lead_vocal", "status": "accepted", "output": {"track_id": 1, "track_index": 1, "name": "Lead Vocal"}},
            {"index": 1, "action": "track.create", "action_id": "track_double_l", "status": "accepted", "output": {"track_id": 2, "track_index": 2, "name": "Double L"}},
            {"index": 2, "action": "track.create", "action_id": "track_double_r", "status": "accepted", "output": {"track_id": 3, "track_index": 3, "name": "Double R"}},
            {"index": 3, "action": "bus.create", "action_id": "bus_vocal_bus", "status": "accepted", "output": {"track_id": 4, "track_index": 4, "name": "Vocal Bus", "is_bus": True}},
            {"index": 4, "action": "send.create", "action_id": "send_lead_vocal_to_vocal_bus", "status": "accepted", "output": {"send_index": 0, "src_track_id": 1, "dst_track_id": 4, "resolved_source_track_id": 1, "resolved_source_track_name": "Lead Vocal", "resolved_destination_track_id": 4, "resolved_destination_track_name": "Vocal Bus", "pre_fader": False}},
            {"index": 5, "action": "send.create", "action_id": "send_double_l_to_vocal_bus", "status": "accepted", "output": {"send_index": 0, "src_track_id": 2, "dst_track_id": 4, "resolved_source_track_id": 2, "resolved_source_track_name": "Double L", "resolved_destination_track_id": 4, "resolved_destination_track_name": "Vocal Bus", "pre_fader": False}},
            {"index": 6, "action": "send.create", "action_id": "send_double_r_to_vocal_bus", "status": "accepted", "output": {"send_index": 0, "src_track_id": 3, "dst_track_id": 4, "resolved_source_track_id": 3, "resolved_source_track_name": "Double R", "resolved_destination_track_id": 4, "resolved_destination_track_name": "Vocal Bus", "pre_fader": False}},
        ],
    }
