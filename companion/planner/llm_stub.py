from __future__ import annotations

from typing import Any

from companion.planner.base import PlannerResult


class LLMPlannerBackend:
    """Placeholder for an LLM-backed planner.

    This is intentionally a stub. When integrated, it will:
    - Build a system prompt from the action schema + project state
    - Call the LLM (e.g. Claude) with the user prompt
    - Parse and Pydantic-validate the returned SessionBuilderPlan JSON
    - Retry on invalid output (up to a configurable limit)
    - Fall back to heuristic on repeated failure
    """

    def plan(
        self,
        prompt: str,
        project_state: dict[str, Any] | None = None,
        clarification_answers: dict[str, str] | None = None,
    ) -> PlannerResult:
        return PlannerResult(
            unsupported=True,
            unsupported_reason="LLM planner not yet implemented — heuristic fallback should be enabled.",
        )
