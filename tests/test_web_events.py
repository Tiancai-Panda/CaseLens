import pytest
from PIL import Image

from caselens.agent import Agent
from caselens.assets import AssetStore
from caselens.config import Settings
from caselens.customer import CustomerService
from caselens.replay import customer_replay_clients
from caselens.schemas import Policy
from caselens.store import EvidenceStore


@pytest.mark.asyncio
async def test_agent_event_sink_receives_sanitized_progress_events(tmp_path):
    image = tmp_path / "photo.png"
    Image.new("RGB", (32, 32), "white").save(image)
    assets = AssetStore(tmp_path)
    assets.register(image)
    clients = customer_replay_clients("irrelevant")
    events = []
    with EvidenceStore(tmp_path / "evidence.sqlite3") as cache:
        service = CustomerService(assets, clients[0], clients[1], cache, Policy(), {"order_id": "O-1"}, Settings(), user_text="包装破损")
        result = await Agent(service, clients[2], mode="replay", event_sink=events.append).run("售后")
    assert result.status == "needs_review"
    assert [event["type"] for event in events][0] == "run_started"
    assert any(event["type"] == "tool_finished" and event["tool"] == "request_information" for event in events)
    assert all("arguments" not in event and "content" not in event for event in events)
