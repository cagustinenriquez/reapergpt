from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)
    debug: bool = Field(default=True)
    reaper_bridge_transport: str = Field(default="dry_run")
    reaper_bridge_url: str = Field(default="http://127.0.0.1:8765")
    bridge_dry_run: bool = Field(default=True)
    reaper_bridge_dir: str = Field(default="")
    llm_provider: str = Field(default="none")
    ollama_base_url: str = Field(default="http://127.0.0.1:11434")
    ollama_model: str = Field(default="qwen2.5:7b-instruct")
    llm_timeout_seconds: float = Field(default=20.0, gt=0)
    llm_allow_heuristic_fallback: bool = Field(default=True)
    plan_session_store_path: str = Field(default="")
    profile_store_path: str = Field(default="")

    @classmethod
    def from_env(cls) -> "Settings":
        default_bridge_dir = ""
        appdata = os.getenv("APPDATA")
        if appdata:
            default_bridge_dir = str(Path(appdata) / "REAPER" / "ReaperGPTBridge")
        default_session_store = ""
        if appdata:
            default_session_store = str(Path(appdata) / "ReaperGPTCompanion" / "plan_sessions.json")
        default_profile_store = ""
        if appdata:
            default_profile_store = str(Path(appdata) / "ReaperGPTCompanion" / "profile.json")

        return cls(
            host=os.getenv("REAPERGPT_HOST", "127.0.0.1"),
            port=int(os.getenv("REAPERGPT_PORT", "8000")),
            debug=_env_bool("REAPERGPT_DEBUG", True),
            reaper_bridge_transport=os.getenv("REAPERGPT_REAPER_BRIDGE_TRANSPORT", "dry_run"),
            reaper_bridge_url=os.getenv(
                "REAPERGPT_REAPER_BRIDGE_URL", "http://127.0.0.1:8765"
            ),
            bridge_dry_run=_env_bool("REAPERGPT_BRIDGE_DRY_RUN", True),
            reaper_bridge_dir=os.getenv("REAPERGPT_REAPER_BRIDGE_DIR", default_bridge_dir),
            llm_provider=os.getenv("REAPERGPT_LLM_PROVIDER", "none"),
            ollama_base_url=os.getenv("REAPERGPT_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("REAPERGPT_OLLAMA_MODEL", "qwen2.5:7b-instruct"),
            llm_timeout_seconds=float(os.getenv("REAPERGPT_LLM_TIMEOUT_SECONDS", "20")),
            llm_allow_heuristic_fallback=_env_bool("REAPERGPT_LLM_ALLOW_HEURISTIC_FALLBACK", True),
            plan_session_store_path=os.getenv("REAPERGPT_PLAN_SESSION_STORE_PATH", default_session_store),
            profile_store_path=os.getenv("REAPERGPT_PROFILE_STORE_PATH", default_profile_store),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
