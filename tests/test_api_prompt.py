from fastapi.testclient import TestClient

import companion.api.routes as api_routes
from companion.api.routes import get_dispatcher, get_reaper_client
from companion.config import Settings, get_settings
from companion.main import app
from companion.models.actions import ActionBatch
from companion.services.action_dispatcher import ActionDispatcher
from companion.models.envelope import ActionDispatchResult, SubmitActionsResponse


def test_project_state_endpoint_returns_structured_snapshot():
    client = TestClient(app)
    response = client.get("/state/project")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["mode"] in {"dry_run", "file_bridge", "http_bridge"}
    assert "project" in body
    assert set(body["project"].keys()) >= {
        "tracks",
        "markers",
        "regions",
        "selection",
        "envelopes_summary",
    }


def test_health_endpoint_includes_planner_config():
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert "planner" in body
    assert "allow_heuristic_fallback" in body["planner"]


def test_plan_endpoint_returns_preview_without_dispatch():
    client = TestClient(app)
    response = client.post("/plan", json={"goal": "play"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["source"] in {"heuristic", "ollama"}
    assert body["project_state_included"] is True
    assert body["plan_id"]
    assert len(body["proposed_actions"]) >= 1
    assert body["proposed_actions"][0]["type"] == "transport.play"


def test_plan_endpoint_returns_clarifying_question_for_unsupported_goal():
    client = TestClient(app)
    response = client.post("/plan", json={"goal": "write a full symphony in my style"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["source"] == "unsupported"
    assert body["proposed_actions"] == []
    assert len(body["clarifying_questions"]) >= 1


def test_execute_plan_endpoint_runs_actions_sequentially():
    client = TestClient(app)
    response = client.post(
        "/execute-plan",
        json={
            "actions": [
                {"type": "transport.play", "params": {}},
                {"type": "transport.stop", "params": {}},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["executed_steps"] == 2
    assert body["failed_step_index"] is None
    assert [r["status"] for r in body["results"]] == ["accepted", "accepted"]


def test_execute_plan_endpoint_returns_final_project_state_snapshot():
    client = TestClient(app)
    response = client.post(
        "/execute-plan",
        json={
            "actions": [
                {"type": "transport.play", "params": {}},
            ]
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["final_project_state"] is not None
    assert body["project_state_error"] is None
    assert body["final_project_state"]["tempo_bpm"] == 120.0


def test_execute_plan_endpoint_reports_project_state_error_when_unavailable():
    class _BrokenClient:
        def __init__(self) -> None:
            self.mode = "dry_run"

        def send_actions(self, batch: ActionBatch) -> dict[str, object]:
            results = [
                {"request_id": action.request_id, "status": "accepted", "detail": "stubbed"}
                for action in batch.actions
            ]
            return {"mode": self.mode, "results": results}

        def get_project_state(self) -> dict[str, object]:
            raise RuntimeError("bridge unreachable")

    fake_client = _BrokenClient()
    app.dependency_overrides[get_dispatcher] = lambda: ActionDispatcher(fake_client)
    app.dependency_overrides[get_reaper_client] = lambda: fake_client
    try:
        client = TestClient(app)
        response = client.post(
            "/execute-plan",
            json={"actions": [{"type": "transport.play", "params": {}}]},
        )
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)
        app.dependency_overrides.pop(get_reaper_client, None)

    assert response.status_code == 200
    body = response.json()
    assert body["final_project_state"] is None
    assert "bridge unreachable" in body["project_state_error"]


def test_execute_plan_endpoint_executes_saved_plan_by_id():
    client = TestClient(app)
    plan = client.post("/plan", json={"goal": "play"})
    plan_body = plan.json()

    response = client.post(
        "/execute-plan",
        json={
            "plan_id": plan_body["plan_id"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["total_steps"] >= 1
    assert body["executed_steps"] >= 1


def test_execute_plan_endpoint_loads_saved_plan_from_disk_after_memory_clear(tmp_path):
    store_path = tmp_path / "plan_sessions.json"
    base_settings = Settings()
    test_settings = base_settings.model_copy(update={"plan_session_store_path": str(store_path)})

    app.dependency_overrides[get_settings] = lambda: test_settings
    try:
        client = TestClient(app)
        plan = client.post("/plan", json={"goal": "play"})
        assert plan.status_code == 200
        plan_id = plan.json()["plan_id"]
        assert store_path.exists()

        api_routes._plan_sessions.clear()
        api_routes._plan_sessions_loaded_for_path = None

        response = client.post("/execute-plan", json={"plan_id": plan_id})
    finally:
        app.dependency_overrides.pop(get_settings, None)
        api_routes._plan_sessions.clear()
        api_routes._plan_sessions_loaded_for_path = None

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["executed_steps"] >= 1


def test_execute_plan_endpoint_rejects_unknown_plan_id():
    client = TestClient(app)
    response = client.post("/execute-plan", json={"plan_id": "missing-plan-id"})

    assert response.status_code == 404
    assert "Plan not found" in response.json()["detail"]


def test_execute_plan_endpoint_stops_on_failure():
    class _FakeClient:
        mode = "dry_run"

    class _FakeDispatcher:
        def __init__(self) -> None:
            self._client = _FakeClient()
            self.calls = 0

        def dispatch_batch(self, batch: ActionBatch) -> SubmitActionsResponse:
            self.calls += 1
            action = batch.actions[0]
            if self.calls == 2:
                return SubmitActionsResponse(
                    success=False,
                    mode="dry_run",
                    results=[
                        ActionDispatchResult(
                            request_id=action.request_id,
                            status="rejected",
                            detail="simulated failure",
                        )
                    ],
                )
            return SubmitActionsResponse(
                success=True,
                mode="dry_run",
                results=[ActionDispatchResult(request_id=action.request_id, status="accepted", detail="ok")],
            )

    fake_dispatcher = _FakeDispatcher()
    app.dependency_overrides[get_dispatcher] = lambda: fake_dispatcher
    try:
        client = TestClient(app)
        response = client.post(
            "/execute-plan",
            json={
                "actions": [
                    {"type": "transport.play", "params": {}},
                    {"type": "transport.stop", "params": {}},
                    {"type": "transport.play", "params": {}},
                ],
                "stop_on_failure": True,
            },
        )
    finally:
        app.dependency_overrides.pop(get_dispatcher, None)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["total_steps"] == 3
    assert body["executed_steps"] == 2
    assert body["failed_step_index"] == 2
    assert [r["status"] for r in body["results"]] == ["accepted", "rejected"]


def test_execute_plan_endpoint_requires_exactly_one_of_plan_id_or_actions():
    client = TestClient(app)
    response = client.post(
        "/execute-plan",
        json={"plan_id": "abc", "actions": [{"type": "transport.play", "params": {}}]},
    )

    assert response.status_code == 422


def test_prompt_endpoint_dispatches_supported_prompt():
    client = TestClient(app)
    response = client.post("/prompt", json={"prompt": "play"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["planner_source"] in {"heuristic", "ollama"}
    assert body["results"][0]["status"] == "accepted"


def test_prompt_endpoint_dispatches_delete_track_prompt():
    client = TestClient(app)
    response = client.post("/prompt", json={"prompt": "delete track 1"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["results"][0]["status"] == "accepted"


def test_prompt_endpoint_rejects_unsupported_prompt():
    client = TestClient(app)
    response = client.post("/prompt", json={"prompt": "compose an opera"})

    assert response.status_code == 400
    assert "supported actions" in response.json()["detail"]


def test_prompt_endpoint_returns_ollama_error_when_fallback_disabled_and_ollama_unreachable():
    base_settings = Settings()
    strict_settings = base_settings.model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_base_url": "http://127.0.0.1:1",
            "llm_timeout_seconds": 0.2,
            "llm_allow_heuristic_fallback": False,
        }
    )
    app.dependency_overrides[get_settings] = lambda: strict_settings
    try:
        client = TestClient(app)
        response = client.post("/prompt", json={"prompt": "play"})
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    assert "heuristic fallback is disabled" in response.json()["detail"]


def test_prompt_endpoint_requests_clarification_for_garage_band_template():
    client = TestClient(app)
    response = client.post(
        "/prompt",
        json={"prompt": "create a template for a garage band (2 guitars, 1 drumset, 1 bass, 1 vocal)"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["needs_clarification"] is True
    assert body["session_id"]
    assert len(body["questions"]) >= 2


def test_prompt_respond_endpoint_builds_garage_band_template_tracks():
    client = TestClient(app)
    first = client.post(
        "/prompt",
        json={"prompt": "create a template for a garage band (2 guitars, 1 drumset, 1 bass, 1 vocal)"},
    )
    session_id = first.json()["session_id"]

    response = client.post(
        "/prompt/respond",
        json={
            "session_id": session_id,
            "answers": {"fx_setup": "yes", "sound_style": "punk"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["planner_source"] == "clarification_template"
    assert len(body["results"]) > 5
    assert all(item["status"] == "accepted" for item in body["results"])


def test_prompt_respond_endpoint_can_build_tracks_without_fx_setup():
    client = TestClient(app)
    first = client.post(
        "/prompt",
        json={"prompt": "create a template for a garage band (2 guitars, 1 drumset, 1 bass, 1 vocal)"},
    )
    session_id = first.json()["session_id"]

    response = client.post(
        "/prompt/respond",
        json={
            "session_id": session_id,
            "answers": {"fx_setup": "no", "sound_style": "clean indie"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["planner_source"] == "clarification_template"
    assert len(body["results"]) == 5
    assert all(item["status"] == "accepted" for item in body["results"])


def test_profile_endpoint_roundtrip_persists_to_disk(tmp_path):
    profile_path = tmp_path / "profile.json"
    base_settings = Settings()
    test_settings = base_settings.model_copy(update={"profile_store_path": str(profile_path)})

    app.dependency_overrides[get_settings] = lambda: test_settings
    try:
        client = TestClient(app)
        initial = client.get("/profile")
        assert initial.status_code == 200
        initial_body = initial.json()
        assert initial_body["ok"] is True
        assert initial_body["profile"]["default_sound_style"] == "classic rock"

        updated = client.put(
            "/profile",
            json={
                "default_sound_style": "punk",
                "default_track_color": "blue",
                "track_naming_prefix": "Guitar",
                "include_fx_by_default": False,
                "preferred_plugins": {"guitar": ["Amp Sim", "EQ"]},
            },
        )
        assert updated.status_code == 200
        updated_body = updated.json()
        assert updated_body["profile"]["default_sound_style"] == "punk"
        assert updated_body["profile"]["default_track_color"] == "blue"
        assert updated_body["profile"]["track_naming_prefix"] == "Guitar"
        assert updated_body["profile"]["include_fx_by_default"] is False
        assert updated_body["profile"]["preferred_plugins"]["guitar"] == ["Amp Sim", "EQ"]

        follow_up = client.get("/profile")
        assert follow_up.status_code == 200
        assert follow_up.json()["profile"]["default_sound_style"] == "punk"
        assert profile_path.exists()
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_plan_endpoint_uses_profile_defaults_for_new_tracks(tmp_path):
    profile_path = tmp_path / "profile.json"
    base_settings = Settings()
    test_settings = base_settings.model_copy(update={"profile_store_path": str(profile_path)})
    app.dependency_overrides[get_settings] = lambda: test_settings
    try:
        client = TestClient(app)
        put_resp = client.put(
            "/profile",
            json={
                "default_track_color": "green",
                "track_naming_prefix": "Rhythm",
            },
        )
        assert put_resp.status_code == 200

        response = client.post("/plan", json={"goal": "create track", "include_project_state": False})
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert [item["type"] for item in body["proposed_actions"]] == ["track.create", "track.set_color"]
    assert body["proposed_actions"][0]["params"] == {"name": "Rhythm 1"}
    assert body["proposed_actions"][1]["params"] == {"color": "green", "track_ref": "last_created"}
