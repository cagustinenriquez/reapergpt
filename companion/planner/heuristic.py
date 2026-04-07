from __future__ import annotations

from typing import Any

from companion.llm.planner import plan_prompt_to_actions
from companion.models.session_builder_plan import (
    BusCreateAction,
    FxInsertAction,
    PlanStep,
    SendCreateAction,
    SessionBuilderPlan,
    TempoSetAction,
    TrackCreateAction,
    TransportAction,
)
from companion.planner.base import PlannerResult


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class HeuristicPlannerBackend:
    """Wraps the existing regex planner and converts its output to SessionBuilderPlan."""

    def plan(
        self,
        prompt: str,
        project_state: dict[str, Any] | None = None,
        clarification_answers: dict[str, str] | None = None,
    ) -> PlannerResult:
        response = plan_prompt_to_actions(
            prompt,
            project_state=project_state,
            clarification_answers=clarification_answers,
        )

        if response.requires_clarification and response.clarification:
            cl = response.clarification
            return PlannerResult(
                requires_clarification=True,
                clarification_id=cl.id,
                clarification_question=cl.question,
                clarification_options=[opt.value for opt in cl.options],
            )

        if not response.ok or response.source == "unsupported":
            return PlannerResult(
                unsupported=True,
                unsupported_reason=response.summary or "prompt not recognised by heuristic planner",
            )

        # Convert BridgePlanSteps back into typed SessionActions.
        actions = []
        plan_steps = []
        counter: dict[str, int] = {}

        for step in response.steps:
            tool = step.tool
            args = step.args

            def _next_id(prefix: str) -> str:
                counter[prefix] = counter.get(prefix, 0) + 1
                return f"{prefix}_{counter[prefix]}"

            if tool == "create_track":
                name = args.get("name") or ""
                action_id = f"track_{_slug(name)}" if name else _next_id("track")
                actions.append(TrackCreateAction(id=action_id, action="track.create", name=name))
                plan_steps.append(PlanStep(id=action_id, title="Create track", description=f"Create track '{name}'." if name else "Create track."))

            elif tool == "create_bus":
                name = args.get("name") or ""
                action_id = f"bus_{_slug(name)}" if name else _next_id("bus")
                actions.append(BusCreateAction(id=action_id, action="bus.create", name=name))
                plan_steps.append(PlanStep(id=action_id, title="Create bus", description=f"Create bus '{name}'." if name else "Create bus."))

            elif tool == "create_send":
                action_id = _next_id("send")
                src = args.get("src") or {}
                dst = args.get("dst") or {}
                pre_fader = bool(args.get("pre_fader"))
                from companion.models.session_builder_plan import EntityRef
                source_ref = _bridge_ref_to_entity_ref(src)
                dest_ref = _bridge_ref_to_entity_ref(dst)
                actions.append(
                    SendCreateAction(
                        id=action_id,
                        action="send.create",
                        source=source_ref,
                        destination=dest_ref,
                        mode="pre-fader" if pre_fader else "post-fader",
                    )
                )
                plan_steps.append(PlanStep(id=action_id, title="Create send"))

            elif tool == "insert_fx":
                action_id = _next_id("fx")
                from companion.models.session_builder_plan import EntityRef
                target_ref = _bridge_ref_to_entity_ref(args.get("track_ref") or {})
                actions.append(
                    FxInsertAction(
                        id=action_id,
                        action="fx.insert",
                        target=target_ref,
                        fx_name=args.get("fx_name") or "",
                    )
                )
                plan_steps.append(PlanStep(id=action_id, title="Insert FX", description=f"Insert {args.get('fx_name')}."))

            elif tool == "project.set_tempo":
                action_id = _next_id("tempo")
                actions.append(TempoSetAction(id=action_id, action="project.set_tempo", bpm=float(args.get("bpm") or 120)))
                plan_steps.append(PlanStep(id=action_id, title="Set tempo", description=f"Set tempo to {args.get('bpm')} BPM."))

            elif tool in {"transport.play", "transport.stop"}:
                action_id = _next_id("transport")
                actions.append(TransportAction(id=action_id, action=tool))  # type: ignore[arg-type]
                plan_steps.append(PlanStep(id=action_id, title=tool))

        return PlannerResult(
            plan=SessionBuilderPlan(
                summary=response.summary,
                steps=plan_steps,
                actions=actions,
            )
        )


def _bridge_ref_to_entity_ref(ref: dict) -> "EntityRef":
    from companion.models.session_builder_plan import EntityRef
    ref_type = ref.get("type")
    value = ref.get("value")
    if ref_type == "track_id" and value is not None:
        return EntityRef(track_id=int(value))
    if ref_type == "track_name" and isinstance(value, str):
        return EntityRef(name=value)
    if isinstance(value, str):
        return EntityRef(name=value)
    # Fallback: use a placeholder name so we don't crash
    return EntityRef(name=str(value or "unknown"))
