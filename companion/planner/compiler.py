from __future__ import annotations

from companion.models.schemas import BridgePlanStep
from companion.models.session_builder_plan import (
    BusCreateAction,
    EntityRef,
    FxInsertAction,
    SendCreateAction,
    SessionBuilderPlan,
    TempoSetAction,
    TrackCreateAction,
    TrackColorAction,
    TrackPanAction,
    TrackRenameAction,
    TransportAction,
)


def compile_plan(plan: SessionBuilderPlan) -> list[BridgePlanStep]:
    """Compile a SessionBuilderPlan into bridge steps ready for execution."""
    created_names: dict[str, str] = {
        action.id: action.name
        for action in plan.actions
        if action.action in {"track.create", "bus.create"}
    }
    steps: list[BridgePlanStep] = []
    for action in plan.actions:
        steps.append(_compile_action(action, created_names))
    return steps


def _compile_action(action: object, created_names: dict[str, str]) -> BridgePlanStep:
    if isinstance(action, TrackCreateAction):
        args: dict = {}
        if action.name:
            args["name"] = action.name
        return BridgePlanStep(tool="create_track", args=args)

    if isinstance(action, BusCreateAction):
        args = {}
        if action.name:
            args["name"] = action.name
        return BridgePlanStep(tool="create_bus", args=args)

    if isinstance(action, SendCreateAction):
        return BridgePlanStep(
            tool="create_send",
            args={
                "src": _resolve_ref(action.source, created_names),
                "dst": _resolve_ref(action.destination, created_names),
                "pre_fader": action.mode == "pre-fader",
            },
        )

    if isinstance(action, FxInsertAction):
        return BridgePlanStep(
            tool="insert_fx",
            args={
                "track_ref": _resolve_ref(action.target, created_names),
                "fx_name": action.fx_name,
            },
        )

    if isinstance(action, TrackColorAction):
        return BridgePlanStep(
            tool="set_track_color",
            args={
                "track_ref": _resolve_ref(action.target, created_names),
                "color": action.color,
            },
        )

    if isinstance(action, TrackRenameAction):
        return BridgePlanStep(
            tool="track.rename",
            args={
                "track_ref": _resolve_ref(action.target, created_names),
                "name": action.name,
            },
        )

    if isinstance(action, TrackPanAction):
        return BridgePlanStep(
            tool="track.set_pan",
            args={
                "track_ref": _resolve_ref(action.target, created_names),
                "pan": action.pan,
            },
        )

    if isinstance(action, TempoSetAction):
        return BridgePlanStep(tool="project.set_tempo", args={"bpm": action.bpm})

    if isinstance(action, TransportAction):
        return BridgePlanStep(tool=action.action, args={})

    raise ValueError(f"unsupported action for compiler: {getattr(action, 'action', type(action))}")


def _resolve_ref(ref: EntityRef, created_names: dict[str, str]) -> dict:
    if ref.track_id is not None:
        return {"type": "track_id", "value": ref.track_id}
    if ref.name:
        return {"type": "track_name", "value": ref.name}
    if ref.action_id:
        if ref.action_id not in created_names:
            raise ValueError(f"unknown action reference '{ref.action_id}'")
        return {"type": "track_name", "value": created_names[ref.action_id]}
    raise ValueError("entity ref is missing a selector")
