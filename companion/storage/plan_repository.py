from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from companion.models.session_builder_plan import SessionBuilderPlan


_SCHEMA = """
CREATE TABLE IF NOT EXISTS saved_plans (
    plan_id     TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    summary     TEXT NOT NULL,
    payload     TEXT NOT NULL,
    prompt      TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
"""


class PlanRepository:
    """SQLite-backed plan store.

    Supports save, get, and list operations with TTL-based expiry.
    Restart-safe: plans survive process restarts as long as the DB file persists.
    """

    def __init__(self, db_path: Path, ttl_seconds: float = 300.0) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._ttl = ttl_seconds
        self._init_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save(
        self,
        plan: SessionBuilderPlan,
        source: str = "heuristic",
        prompt: str | None = None,
    ) -> str:
        plan_id = str(uuid.uuid4())
        now = time.time()
        expires_at = now + self._ttl
        payload = plan.model_dump_json()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO saved_plans (plan_id, source, summary, payload, prompt, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (plan_id, source, plan.summary, payload, prompt, now, expires_at),
            )
        return plan_id

    def get(self, plan_id: str) -> tuple[SessionBuilderPlan, dict[str, Any]] | None:
        """Return (plan, metadata) or None.

        Returns None both when the plan_id was never stored AND when it has expired.
        Callers can distinguish by calling is_expired() first if needed.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload, source, prompt, created_at, expires_at FROM saved_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        payload, source, prompt, created_at, expires_at = row
        if time.time() > expires_at:
            return None
        try:
            plan = SessionBuilderPlan.model_validate_json(payload)
        except Exception:
            return None
        meta: dict[str, Any] = {
            "source": source,
            "prompt": prompt,
            "created_at": created_at,
            "expires_at": expires_at,
        }
        return plan, meta

    def is_expired(self, plan_id: str) -> bool:
        """True if plan_id exists but is past its TTL."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM saved_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            return False
        return time.time() > row[0]

    def prune(self) -> int:
        """Delete all expired plans; returns count removed."""
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM saved_plans WHERE expires_at <= ?", (now,))
            return cursor.rowcount

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Module-level singleton (reset-able for tests)
# ---------------------------------------------------------------------------

_repo: PlanRepository | None = None


def get_plan_repository(db_path: Path | None = None, ttl_seconds: float = 300.0) -> PlanRepository:
    global _repo
    if _repo is None:
        from companion.config import get_settings
        settings = get_settings()
        _repo = PlanRepository(
            db_path=db_path or settings.db_path,
            ttl_seconds=ttl_seconds or settings.saved_plan_ttl_seconds,
        )
    return _repo


def reset_plan_repository() -> None:
    global _repo
    _repo = None
