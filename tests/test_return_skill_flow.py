import pytest
from test_cases import photo

from caselens.cases import run_customer_case
from caselens.config import Settings
from caselens.schemas import Policy


@pytest.mark.asyncio
async def test_return_request_is_not_assumed_to_be_product_damage(tmp_path):
    result = await run_customer_case([photo(tmp_path)], user_text="商品完好，但是我不喜欢，想退货", goal="处理退货", metadata={"order_id": "R-1"}, settings=Settings(state_dir=tmp_path / "state"), policy=Policy(), replay="return")
    assert result.intent.issues == ["return_request"]
    assert result.status == "needs_review"
    assert result.resolutions[0].effective_action == "human_review"
    assert result.resolutions[0].executed is False
    assert any(s["name"] == "return-request" for s in result.skills)
    assert "policy" in result.memory["policy_version"] or result.memory["policy_version"] == "after-sales-v1"


@pytest.mark.asyncio
async def test_split_shipment_skill_prevents_premature_reship(tmp_path):
    result = await run_customer_case([photo(tmp_path)], user_text="买三件收到两件，另一包在路上", goal="核实数量", metadata={"order_id": "S-1"}, settings=Settings(state_dir=tmp_path / "state"), policy=Policy(), replay="split")
    assert result.status == "needs_review"
    assert any(step.call.name == "check_quantity" and step.result.data.get("status") == "pending_shipment" for step in result.steps)
    assert result.resolutions[0].effective_action == "human_review"
    assert result.resolutions[0].executed is False
