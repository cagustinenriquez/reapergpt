from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from companion.config import Settings, get_settings
from companion.daws.reaper.client import ReaperBridgeClient
from companion.llm.planner import plan_prompt_to_actions
from companion.models.envelope import (
    PromptDispatchRequest,
    SubmitActionsRequest,
    SubmitActionsResponse,
)
from companion.services.action_dispatcher import ActionDispatcher

router = APIRouter()


def get_reaper_client(settings: Settings = Depends(get_settings)) -> ReaperBridgeClient:
    return ReaperBridgeClient(
        base_url=settings.reaper_bridge_url,
        dry_run=settings.bridge_dry_run,
        transport=settings.reaper_bridge_transport,
        bridge_dir=settings.reaper_bridge_dir or None,
    )


def get_dispatcher(client: ReaperBridgeClient = Depends(get_reaper_client)) -> ActionDispatcher:
    return ActionDispatcher(client)


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "service": "reapergpt-companion",
        "bridge": {
            "url": settings.reaper_bridge_url,
            "mode": settings.reaper_bridge_transport,
            "dir": settings.reaper_bridge_dir or None,
        },
    }


@router.post("/actions", response_model=SubmitActionsResponse)
def submit_actions(
    payload: SubmitActionsRequest,
    dispatcher: ActionDispatcher = Depends(get_dispatcher),
) -> SubmitActionsResponse:
    return dispatcher.dispatch_batch(payload.batch)


@router.post("/prompt", response_model=SubmitActionsResponse)
def submit_prompt(
    payload: PromptDispatchRequest,
    settings: Settings = Depends(get_settings),
    dispatcher: ActionDispatcher = Depends(get_dispatcher),
) -> SubmitActionsResponse:
    planned = plan_prompt_to_actions(payload.prompt, settings)
    if planned.batch is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Prompt could not be mapped to supported actions yet. "
                "Try: play, stop, tempo 120, solo track 2, mute track 3, "
                "select track 1, create regions for a pop song, create regions for a rock song"
            ),
        )
    return dispatcher.dispatch_batch(planned.batch)
