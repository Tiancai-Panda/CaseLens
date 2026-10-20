import pytest
from test_agent import setup_case

from caselens.agent import Agent


@pytest.mark.asyncio
async def test_graph_routes_reinspection_and_publishes_nodes(tmp_path):
    service, controller, cache, _ = setup_case(tmp_path, "blurred")
    events = []
    try:
        result = await Agent(service, controller, mode="replay", event_sink=events.append).run("核实包装")
    finally:
        cache.close()
    nodes = [event["node"] for event in events if event["type"] == "graph_node"]
    assert nodes[0] == "bootstrap"
    assert nodes.count("controller") == 6
    assert nodes.count("tools") == 6
    assert nodes[-1] == "finalize"
    assert result.status == "completed"
    assert result.runtime == "langgraph"


@pytest.mark.asyncio
async def test_graph_counts_controller_rounds_and_preserves_budget(tmp_path):
    from caselens.replay import ReplayController
    service, _, cache, _ = setup_case(tmp_path)
    controller = ReplayController([[{"name": "shell", "arguments": {}}]] * 10)
    events = []
    try:
        result = await Agent(service, controller, mode="replay", event_sink=events.append).run("检查")
    finally:
        cache.close()
    assert sum(event.get("node") == "controller" for event in events) == 6
    assert len(result.steps) == 6
    assert result.status == "failed"
