from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from companion.config import Settings, get_settings
from companion.models.schemas import PlanStep


class ActionExecutionError(RuntimeError):
    """Raised when the bridge cannot carry out a requested tool."""


class ReaperBridgeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bridge_root = settings.bridge_root
        self._bridge_root.mkdir(parents=True, exist_ok=True)

    @property
    def mode(self) -> str:
        return self._settings.bridge_mode

    def _atomic_write_json(self, path: Path, payload: dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _read_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _default_state(self) -> dict[str, Any]:
        return {
            "project_name": "REAPER File Bridge",
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
            "bridge_connected": False,
        }

    def _coerce_state(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        state = self._default_state()
        if isinstance(payload, dict):
            state.update(payload)
        return state

    def get_state(self) -> dict[str, Any]:
        state_path = self._settings.bridge_state_path
        if not state_path.exists():
            return self._default_state()
        try:
            return self._coerce_state(self._read_json(state_path))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return self._default_state()

    def execute_plan(self, steps: list[PlanStep]) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        self._clear_stale_request()
        self._clear_stale_result(request_id=request_id)
        payload = {
            "request_id": request_id,
            "created_at": time.time(),
            "steps": [step.model_dump() for step in steps],
        }
        self._atomic_write_json(self._settings.bridge_request_path, payload)
        return self._wait_for_result(request_id)

    def _clear_stale_result(self, request_id: str) -> None:
        result_path = self._settings.bridge_result_path
        if not result_path.exists():
            return
        try:
            payload = self._read_json(result_path)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            result_path.unlink(missing_ok=True)
            return
        if payload.get("request_id") != request_id:
            result_path.unlink(missing_ok=True)

    def _clear_stale_request(self) -> None:
        request_path = self._settings.bridge_request_path
        if not request_path.exists():
            return
        try:
            payload = self._read_json(request_path)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            request_path.unlink(missing_ok=True)
            return
        created_at = float(payload.get("created_at", 0.0) or 0.0)
        if created_at <= 0.0 or (time.time() - created_at) > self._settings.bridge_timeout_seconds:
            request_path.unlink(missing_ok=True)

    def _wait_for_result(self, request_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self._settings.bridge_timeout_seconds
        result_path = self._settings.bridge_result_path
        while time.monotonic() < deadline:
            if result_path.exists():
                try:
                    payload = self._read_json(result_path)
                except (json.JSONDecodeError, OSError, TypeError, ValueError):
                    time.sleep(self._settings.bridge_poll_interval_ms / 1000.0)
                    continue
                if payload.get("request_id") != request_id:
                    if time.time() - float(payload.get("created_at", 0.0) or 0.0) > self._settings.bridge_timeout_seconds:
                        result_path.unlink(missing_ok=True)
                    time.sleep(self._settings.bridge_poll_interval_ms / 1000.0)
                    continue
                return payload
            time.sleep(self._settings.bridge_poll_interval_ms / 1000.0)
        raise ActionExecutionError(
            f"timed out waiting for REAPER bridge result at {self._settings.bridge_result_path}"
        )


_bridge_client: ReaperBridgeClient | None = None


def get_bridge_client(settings: Settings | None = None) -> ReaperBridgeClient:
    global _bridge_client
    if _bridge_client is None:
        _bridge_client = ReaperBridgeClient(settings or get_settings())
    return _bridge_client


def reset_bridge_client() -> None:
    global _bridge_client
    _bridge_client = None
