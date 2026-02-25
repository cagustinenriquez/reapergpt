from __future__ import annotations

from companion.daws.reaper.client import ReaperBridgeClient
from companion.models.actions import ActionBatch, ActionType
from companion.models.envelope import ActionDispatchResult, SubmitActionsResponse


class ActionDispatcher:
    def __init__(self, client: ReaperBridgeClient) -> None:
        self._client = client
        self._allowlist = {action.value for action in ActionType}

    def dispatch_batch(self, batch: ActionBatch) -> SubmitActionsResponse:
        rejected: list[ActionDispatchResult] = []
        accepted_actions = []

        for action in batch.actions:
            if action.type.value not in self._allowlist:
                rejected.append(
                    ActionDispatchResult(
                        request_id=action.request_id,
                        status="rejected",
                        detail=f"Action not allowed: {action.type.value}",
                    )
                )
                continue
            accepted_actions.append(action)

        bridge_results: list[ActionDispatchResult] = []
        if accepted_actions:
            bridge_payload = self._client.send_actions(ActionBatch(actions=accepted_actions))
            bridge_results = [
                ActionDispatchResult(
                    request_id=item["request_id"],
                    status=item["status"],
                    detail=item.get("detail"),
                )
                for item in bridge_payload["results"]
            ]

        all_results = [*bridge_results, *rejected]
        success = all(result.status == "accepted" for result in all_results) if all_results else False

        return SubmitActionsResponse(
            success=success,
            mode=bridge_payload["mode"] if accepted_actions else self._client.mode,
            results=all_results,
        )
