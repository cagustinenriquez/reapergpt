from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_provider: str = Field("heuristic")
    llm_allow_heuristic_fallback: bool = Field(True)
    llm_timeout_seconds: int = Field(10)
    debug: bool = Field(False)
    bridge_mode: str = Field("file")
    bridge_root: Path = Field(Path("data") / "reaper_bridge")
    bridge_request_filename: str = Field("pending_plan.json")
    bridge_result_filename: str = Field("execution_result.json")
    bridge_state_filename: str = Field("project_state.json")
    bridge_poll_interval_ms: int = Field(200)
    bridge_timeout_seconds: float = Field(10.0)
    saved_plan_ttl_seconds: float = Field(300.0)

    model_config = ConfigDict(
        env_file=".env",
        env_prefix="REAPERGPT_",
        extra="ignore",
        frozen=True,
    )

    @field_validator("debug", mode="before")
    @classmethod
    def _normalize_debug(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized not in {"false", "0", "no", "off", "", "release"}
        return bool(value)

    @field_validator("bridge_root", mode="before")
    @classmethod
    def _normalize_bridge_root(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value
        if isinstance(value, str) and value.strip():
            return Path(value.strip())
        return Path("data") / "reaper_bridge"

    @property
    def bridge_request_path(self) -> Path:
        return self.bridge_root / self.bridge_request_filename

    @property
    def bridge_result_path(self) -> Path:
        return self.bridge_root / self.bridge_result_filename

    @property
    def bridge_state_path(self) -> Path:
        return self.bridge_root / self.bridge_state_filename


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
