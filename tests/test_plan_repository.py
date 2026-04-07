"""Tests for the SQLite-backed plan repository."""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from companion.models.session_builder_plan import (
    BusCreateAction,
    SendCreateAction,
    EntityRef,
    SessionBuilderPlan,
    TrackCreateAction,
)
from companion.storage.plan_repository import PlanRepository


def _make_db(tmp_path: Path, ttl: float = 300.0) -> PlanRepository:
    return PlanRepository(db_path=tmp_path / f"{uuid.uuid4().hex}.db", ttl_seconds=ttl)


def _vocal_plan() -> SessionBuilderPlan:
    return SessionBuilderPlan(
        summary="Create vocal session.",
        actions=[
            TrackCreateAction(id="track_lead_vocal", action="track.create", name="Lead Vocal"),
            BusCreateAction(id="bus_vocal_bus", action="bus.create", name="Vocal Bus"),
            SendCreateAction(
                id="send_1",
                action="send.create",
                source=EntityRef(action_id="track_lead_vocal"),
                destination=EntityRef(action_id="bus_vocal_bus"),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Basic CRUD
# ---------------------------------------------------------------------------

def test_save_and_get_round_trips_plan(tmp_path):
    repo = _make_db(tmp_path)
    plan = _vocal_plan()

    plan_id = repo.save(plan, source="heuristic", prompt="set up vocals")
    result = repo.get(plan_id)

    assert result is not None
    retrieved_plan, meta = result
    assert retrieved_plan.summary == plan.summary
    assert len(retrieved_plan.actions) == 3
    assert retrieved_plan.actions[0].action == "track.create"
    assert meta["source"] == "heuristic"
    assert meta["prompt"] == "set up vocals"


def test_get_returns_none_for_unknown_plan_id(tmp_path):
    repo = _make_db(tmp_path)
    assert repo.get("nonexistent-plan-id") is None


def test_is_expired_returns_false_for_unknown_plan_id(tmp_path):
    repo = _make_db(tmp_path)
    assert repo.is_expired("nonexistent-plan-id") is False


def test_is_expired_returns_false_for_fresh_plan(tmp_path):
    repo = _make_db(tmp_path)
    plan_id = repo.save(_vocal_plan())
    assert repo.is_expired(plan_id) is False


# ---------------------------------------------------------------------------
# TTL / expiry
# ---------------------------------------------------------------------------

def test_get_returns_none_after_ttl_expires(tmp_path):
    repo = _make_db(tmp_path, ttl=0.01)
    plan_id = repo.save(_vocal_plan())
    time.sleep(0.05)
    assert repo.get(plan_id) is None


def test_is_expired_returns_true_after_ttl_expires(tmp_path):
    repo = _make_db(tmp_path, ttl=0.01)
    plan_id = repo.save(_vocal_plan())
    time.sleep(0.05)
    assert repo.is_expired(plan_id) is True


def test_prune_removes_expired_plans(tmp_path):
    repo = _make_db(tmp_path, ttl=0.01)
    plan_id = repo.save(_vocal_plan())
    time.sleep(0.05)
    removed = repo.prune()
    assert removed == 1
    assert repo.get(plan_id) is None


def test_prune_leaves_non_expired_plans(tmp_path):
    repo = _make_db(tmp_path, ttl=300.0)
    plan_id = repo.save(_vocal_plan())
    removed = repo.prune()
    assert removed == 0
    assert repo.get(plan_id) is not None


# ---------------------------------------------------------------------------
# Restart-safety
# ---------------------------------------------------------------------------

def test_plan_survives_new_repository_instance(tmp_path):
    """Simulates a process restart: plan written by instance A is readable by instance B."""
    db_path = tmp_path / "shared.db"
    repo_a = PlanRepository(db_path=db_path, ttl_seconds=300.0)
    plan_id = repo_a.save(_vocal_plan(), source="heuristic", prompt="vocals")

    # Simulate restart: create a fresh instance pointing at the same file.
    repo_b = PlanRepository(db_path=db_path, ttl_seconds=300.0)
    result = repo_b.get(plan_id)

    assert result is not None
    plan, meta = result
    assert plan.summary == "Create vocal session."
    assert meta["prompt"] == "vocals"


def test_is_expired_survives_new_repository_instance(tmp_path):
    db_path = tmp_path / "shared_expired.db"
    repo_a = PlanRepository(db_path=db_path, ttl_seconds=0.01)
    plan_id = repo_a.save(_vocal_plan())
    time.sleep(0.05)

    repo_b = PlanRepository(db_path=db_path, ttl_seconds=0.01)
    assert repo_b.is_expired(plan_id) is True


# ---------------------------------------------------------------------------
# Serialisation edge cases
# ---------------------------------------------------------------------------

def test_plan_with_all_action_types_round_trips(tmp_path):
    from companion.models.session_builder_plan import (
        FxInsertAction,
        TempoSetAction,
        TransportAction,
        TrackColorAction,
        TrackPanAction,
        TrackRenameAction,
    )
    plan = SessionBuilderPlan(
        summary="All actions.",
        actions=[
            TrackCreateAction(id="t1", action="track.create", name="Guitar"),
            TrackRenameAction(id="t2", action="track.rename", target=EntityRef(name="Guitar"), name="Gtr"),
            TrackColorAction(id="t3", action="track.color", target=EntityRef(name="Gtr"), color="#ff0000"),
            TrackPanAction(id="t4", action="track.pan", target=EntityRef(name="Gtr"), pan=-0.5),
            BusCreateAction(id="b1", action="bus.create", name="Mix Bus"),
            SendCreateAction(
                id="s1",
                action="send.create",
                source=EntityRef(name="Gtr"),
                destination=EntityRef(name="Mix Bus"),
                mode="pre-fader",
            ),
            FxInsertAction(id="fx1", action="fx.insert", target=EntityRef(name="Gtr"), fx_name="ReaEQ"),
            TempoSetAction(id="tempo1", action="project.set_tempo", bpm=140.0),
            TransportAction(id="tp1", action="transport.play"),
        ],
    )
    repo = _make_db(tmp_path)
    plan_id = repo.save(plan)
    result = repo.get(plan_id)

    assert result is not None
    retrieved, _ = result
    assert len(retrieved.actions) == 9
    assert retrieved.actions[7].action == "project.set_tempo"
    assert retrieved.actions[7].bpm == 140.0  # type: ignore[attr-defined]
    assert retrieved.actions[8].action == "transport.play"
