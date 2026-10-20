import json

import pytest
from test_agent import setup_case

from caselens.agent import Agent
from caselens.context import ContextManager
from caselens.errors import ClientError
from caselens.replay import ReplayController
from caselens.schemas import ToolCall


@pytest.mark.asyncio
async def test_exhausted_controller_transport_is_not_retried_by_graph(tmp_path):
    service, _, cache, _ = setup_case(tmp_path)
    class FailedController:
        attempts = 0
        async def next_action(self, messages, *, tool_schemas):
            self.attempts += 1
            raise ClientError("provider_unavailable", "Client retry budget already exhausted", retryable=True)
    controller = FailedController()
    try:
        result = await Agent(service, controller, mode="replay").run("检查")
        assert controller.attempts == 1
        assert result.status == "failed"
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_invalid_controller_call_feedback_survives_context_rebuild(tmp_path):
    service, _, cache, _ = setup_case(tmp_path)
    followup = ReplayController([[{"name": "request_information", "arguments": {"fields": ["photo"]}}]])
    class RepairController:
        seen = []
        async def next_action(self, messages, *, tool_schemas):
            self.seen.append(messages)
            if len(self.seen) == 1:
                raise ClientError("invalid_tool_calls", "malformed native call")
            return await followup.next_action(messages, tool_schemas=tool_schemas)
    controller = RepairController()
    try:
        result = await Agent(service, controller, mode="replay").run("检查")
        assert result.status == "needs_review"
        assert "invalid_tool_calls" in json.dumps(controller.seen[1])
        assert "invalid_tool_calls" in result.memory["controller_errors"]
    finally:
        cache.close()


def test_rolling_memory_keeps_earlier_actions_and_errors():
    manager = ContextManager()
    prior = {"prior_actions": [{"tool": "first"}], "actions": [{"tool": "second"}], "prior_errors": ["first-error"], "errors": ["second-error"]}
    _, _, memory = manager.prepare(system="rules", goal="继续", state={}, schemas=[], groups=[], skills=[], prior_memory=prior)
    assert [a["tool"] for a in memory["prior_actions"]] == ["first", "second"]
    assert memory["prior_errors"] == ["first-error", "second-error"]


@pytest.mark.asyncio
async def test_malformed_quantity_evidence_ids_are_tool_error_not_crash(tmp_path):
    service, _, cache, _ = setup_case(tmp_path)
    try:
        result = await service.registry().execute(ToolCall(call_id="bad", name="check_quantity", arguments={"data": {"evidence_ids": [{}]}}))
        assert not result.ok
        assert result.error_code == "invalid_evidence"
    finally:
        cache.close()
