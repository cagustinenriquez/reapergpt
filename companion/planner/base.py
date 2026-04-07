from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from companion.models.session_builder_plan import SessionBuilderPlan


@dataclass
class PlannerResult:
    plan: SessionBuilderPlan | None = None
    # When the planner needs more information before producing a plan.
    requires_clarification: bool = False
    clarification_id: str | None = None
    clarification_question: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    # When the planner could not handle the prompt at all.
    unsupported: bool = False
    unsupported_reason: str = ""


class PlannerBackend(Protocol):
    """Minimal contract for any planning backend."""

    def plan(
        self,
        prompt: str,
        project_state: dict[str, Any] | None = None,
        clarification_answers: dict[str, str] | None = None,
    ) -> PlannerResult: ...
