from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from companion.models.actions import ActionBatch


class SubmitActionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch: ActionBatch


class ActionDispatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: Literal["accepted", "rejected"]
    detail: str | None = None


class SubmitActionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    mode: Literal["dry_run", "http_bridge", "file_bridge"]
    results: list[ActionDispatchResult] = Field(default_factory=list)


class PromptDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=2000)
