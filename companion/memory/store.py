from __future__ import annotations

import json
from pathlib import Path

from companion.config import Settings
from companion.models.envelope import UserProfilePreferences, UserProfileUpdateRequest


def _profile_store_file(settings: Settings) -> Path | None:
    raw = (settings.profile_store_path or "").strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def load_user_profile(settings: Settings) -> UserProfilePreferences:
    store_file = _profile_store_file(settings)
    if store_file is None or not store_file.exists():
        return UserProfilePreferences()

    try:
        raw = store_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return UserProfilePreferences()

    if not isinstance(data, dict):
        return UserProfilePreferences()
    payload = data.get("profile")
    if not isinstance(payload, dict):
        return UserProfilePreferences()
    try:
        return UserProfilePreferences.model_validate(payload)
    except Exception:
        return UserProfilePreferences()


def save_user_profile(settings: Settings, profile: UserProfilePreferences) -> None:
    store_file = _profile_store_file(settings)
    if store_file is None:
        return
    try:
        store_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = store_file.with_suffix(store_file.suffix + ".tmp")
        payload = {"profile": profile.model_dump(mode="python")}
        tmp_file.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
        tmp_file.replace(store_file)
    except OSError:
        # Non-fatal for MVP: requests still return in-memory state for the current call.
        return


def apply_user_profile_update(
    current: UserProfilePreferences, update: UserProfileUpdateRequest
) -> UserProfilePreferences:
    changed: dict[str, object] = {}
    if update.preferred_plugins is not None:
        normalized: dict[str, list[str]] = {}
        for key, plugins in update.preferred_plugins.items():
            safe_key = (key or "").strip().lower()
            if not safe_key:
                continue
            clean_plugins = [name.strip() for name in plugins if isinstance(name, str) and name.strip()]
            if clean_plugins:
                normalized[safe_key] = clean_plugins
        changed["preferred_plugins"] = normalized
    if update.default_sound_style is not None:
        changed["default_sound_style"] = update.default_sound_style.strip().lower()
    if update.track_naming_prefix is not None:
        changed["track_naming_prefix"] = update.track_naming_prefix.strip()
    if update.default_track_color is not None:
        changed["default_track_color"] = update.default_track_color.strip().lower()
    if update.routing_template_default is not None:
        changed["routing_template_default"] = update.routing_template_default.strip().lower()
    if update.include_fx_by_default is not None:
        changed["include_fx_by_default"] = update.include_fx_by_default
    return current.model_copy(update=changed)
