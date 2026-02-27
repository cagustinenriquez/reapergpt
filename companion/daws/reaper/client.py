from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from companion.models.actions import ActionBatch


class ReaperBridgeClient:
    def __init__(
        self,
        base_url: str,
        dry_run: bool = True,
        timeout_seconds: float = 3.0,
        transport: str | None = None,
        bridge_dir: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.dry_run = dry_run
        self.timeout_seconds = timeout_seconds
        self.transport = (transport or ("dry_run" if dry_run else "http")).strip().lower()
        if self.transport in {"file", "file_bridge"}:
            self.dry_run = False
        elif self.transport in {"http", "http_bridge"}:
            self.dry_run = False
        else:
            self.transport = "dry_run"
            self.dry_run = True
        self.bridge_dir = Path(bridge_dir).expanduser() if bridge_dir else None

    @property
    def mode(self) -> str:
        if self.transport in {"dry_run", "stub"}:
            return "dry_run"
        if self.transport in {"file", "file_bridge"}:
            return "file_bridge"
        return "http_bridge"

    def _command_file(self) -> Path:
        if not self.bridge_dir:
            raise RuntimeError("File bridge requires REAPERGPT_REAPER_BRIDGE_DIR")
        return self.bridge_dir / "commands.txt"

    def _response_file(self) -> Path:
        if not self.bridge_dir:
            raise RuntimeError("File bridge requires REAPERGPT_REAPER_BRIDGE_DIR")
        return self.bridge_dir / "responses.txt"

    def _state_file(self) -> Path:
        if not self.bridge_dir:
            raise RuntimeError("File bridge requires REAPERGPT_REAPER_BRIDGE_DIR")
        return self.bridge_dir / "project_state.json"

    def _send_actions_file_bridge(self, batch: ActionBatch) -> dict[str, Any]:
        unsupported = []
        supported = []
        supported_types = {
            "transport.play",
            "transport.stop",
            "project.set_tempo",
            "project.render_region",
            "regions.create_song_form",
            "track.create",
            "track.delete",
            "track.select",
            "track.set_name",
            "track.set_color",
            "track.set_volume",
            "track.set_pan",
            "track.set_input",
            "track.set_stereo",
            "track.set_monitoring",
            "track.set_record_mode",
            "track.create_send",
            "track.create_receive",
            "track.mute",
            "track.solo",
            "track.record_arm",
            "fx.add",
            "automation.pan_ramp",
            "automation.volume_ramp",
            "reaper.action",
        }
        for action in batch.actions:
            if action.type.value in supported_types:
                supported.append(action)
            else:
                unsupported.append(action)

        results = []
        for action in unsupported:
            results.append(
                {
                    "request_id": action.request_id,
                    "status": "rejected",
                    "detail": (
                        "file bridge MVP supports transport.play, transport.stop, "
                        "project.set_tempo, project.render_region, regions.create_song_form, track.create, track.delete, "
                        "track.select, track.set_name, track.set_color, track.set_volume, "
                        "track.set_pan, track.set_input, track.set_stereo, track.set_monitoring, "
                        "track.set_record_mode, track.create_send, track.create_receive, "
                        "track.mute, track.solo, track.record_arm, fx.add, "
                        "automation.pan_ramp, automation.volume_ramp, and reaper.action"
                    ),
                }
            )

        if not supported:
            return {"mode": self.mode, "results": results}

        bridge_dir = self.bridge_dir
        if bridge_dir is None:
            raise RuntimeError("File bridge requires REAPERGPT_REAPER_BRIDGE_DIR")
        bridge_dir.mkdir(parents=True, exist_ok=True)
        command_file = self._command_file()
        response_file = self._response_file()
        batch_id = str(uuid4())

        if response_file.exists():
            try:
                response_file.unlink()
            except OSError:
                pass

        lines = [f"batch_id={batch_id}"]
        for action in supported:
            params_json = json.dumps(action.params, separators=(",", ":"), sort_keys=True)
            lines.append(f"{action.request_id}\t{action.type.value}\t{params_json}")
        command_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        deadline = time.time() + self.timeout_seconds
        while time.time() < deadline:
            if response_file.exists():
                raw = response_file.read_text(encoding="utf-8", errors="replace")
                parsed = self._parse_file_bridge_response(raw, batch_id=batch_id)
                if parsed is not None:
                    results.extend(parsed)
                    return {"mode": self.mode, "results": results}
            time.sleep(0.05)

        for action in supported:
            results.append(
                {
                    "request_id": action.request_id,
                    "status": "rejected",
                    "detail": "Timed out waiting for REAPER file bridge response",
                }
            )
        return {"mode": self.mode, "results": results}

    @staticmethod
    def _parse_file_bridge_response(raw: str, batch_id: str) -> list[dict[str, str]] | None:
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines or lines[0] != f"batch_id={batch_id}":
            return None
        results: list[dict[str, str]] = []
        for line in lines[1:]:
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            request_id = parts[0]
            status = parts[1]
            detail = parts[2] if len(parts) > 2 else None
            item = {"request_id": request_id, "status": status}
            if detail:
                item["detail"] = detail
            results.append(item)
        return results

    def ping(self) -> dict[str, Any]:
        if self.mode == "dry_run":
            return {"ok": True, "mode": self.mode, "detail": "stub"}
        if self.mode == "file_bridge":
            bridge_dir = self.bridge_dir
            if bridge_dir is None:
                return {"ok": False, "mode": self.mode, "detail": "bridge_dir not configured"}
            bridge_dir.mkdir(parents=True, exist_ok=True)
            return {"ok": True, "mode": self.mode, "detail": str(bridge_dir)}

        response = httpx.get(f"{self.base_url}/health", timeout=self.timeout_seconds)
        response.raise_for_status()
        return {"ok": True, "mode": self.mode, "detail": "reachable"}

    def get_project_state(self) -> dict[str, Any]:
        if self.mode == "dry_run":
            return {
                "ok": True,
                "mode": self.mode,
                "project": {
                    "project_name": "Dry Run Project",
                    "project_path": "",
                    "tempo_bpm": 120.0,
                    "play_state": "stopped",
                    "tracks": [],
                    "markers": [],
                    "regions": [],
                    "selection": {
                        "selected_track_index": None,
                        "selected_item_count": 0,
                        "selected_region_index": None,
                    },
                    "envelopes_summary": {
                        "volume_envelopes": 0,
                        "pan_envelopes": 0,
                        "other_envelopes": 0,
                    },
                },
            }

        if self.mode == "file_bridge":
            state_file = self._state_file()
            if not state_file.exists():
                raise RuntimeError("REAPER file bridge project_state.json not found (is the bridge script running?)")
            raw = state_file.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise RuntimeError("Invalid project state snapshot (expected JSON object)")
            data.setdefault("ok", True)
            data.setdefault("mode", self.mode)
            return data

        response = httpx.get(
            f"{self.base_url}/state/project",
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Invalid HTTP bridge project state response")
        data.setdefault("mode", self.mode)
        data.setdefault("ok", True)
        return data

    def send_actions(self, batch: ActionBatch) -> dict[str, Any]:
        if self.mode == "dry_run":
            return {
                "mode": self.mode,
                "results": [
                    {
                        "request_id": action.request_id,
                        "status": "accepted",
                        "detail": f"stubbed dispatch for {action.type.value}",
                    }
                    for action in batch.actions
                ],
            }
        if self.mode == "file_bridge":
            return self._send_actions_file_bridge(batch)

        payload = {
            "actions": [
                {
                    "request_id": action.request_id,
                    "type": action.type.value,
                    "params": action.params,
                }
                for action in batch.actions
            ]
        }
        response = httpx.post(
            f"{self.base_url}/actions",
            json=payload,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "mode": self.mode,
            "results": data.get("results", []),
        }
