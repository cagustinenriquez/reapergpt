from __future__ import annotations

from typing import Any

from companion.models.session_builder_plan import PromptRequest
from companion.planner.base import PlannerResult
from companion.session_builder import build_mock_prompt_plan


class SessionPlannerBackend:
    """Thin wrapper around the typed session_builder for the /prompt path."""

    def plan(
        self,
        prompt: str,
        project_state: dict[str, Any] | None = None,
        clarification_answers: dict[str, str] | None = None,
    ) -> PlannerResult:
        try:
            plan = build_mock_prompt_plan(PromptRequest(prompt=prompt))
        except ValueError as exc:
            return PlannerResult(unsupported=True, unsupported_reason=str(exc))
        return PlannerResult(plan=plan)
