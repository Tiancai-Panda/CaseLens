import pytest
from test_cases import photo

from caselens.cases import CaseStore, run_customer_case
from caselens.config import Settings
from caselens.schemas import Policy


@pytest.mark.asyncio
async def test_context_budget_failure_saves_case_and_guidance_without_model_call(tmp_path):
    settings = Settings(state_dir=tmp_path / "state", context_max_input_tokens=1000, context_output_reserve=100)
    result = await run_customer_case([photo(tmp_path)], user_text="包装破损", goal="帮助售后", metadata={"order_id": "O1"}, settings=settings, policy=Policy(), replay="clear")
    assert result.status == "needs_review"
    assert any(error["code"] == "context_budget_exceeded" for error in result.errors)
    assert all(call.model != "recorded-controller" for call in result.calls)
    with CaseStore(settings.state_dir) as store:
        assert store.get(result.case_id)["running"] is False


@pytest.mark.asyncio
async def test_resume_keeps_action_memory_but_replacement_invalidates_it(tmp_path):
    settings = Settings(state_dir=tmp_path / "state")
    first = await run_customer_case([photo(tmp_path)], user_text="包装问题", goal="帮助售后", metadata={}, settings=settings, policy=Policy(), replay="clear")
    second = await run_customer_case([], user_text="补充订单", goal="", metadata={"order_id": "O1"}, settings=settings, policy=None, replay="clear", case_id=first.case_id)
    assert second.memory.get("prior_actions")
    assert second.memory["evidence_ids"] == first.memory["evidence_ids"]
    with CaseStore(settings.state_dir) as store:
        record = store.get(first.case_id)
        assert record["snapshot"]["memory"]["submission"] == 2
        source_id = record["images"][0]["asset_id"]
        record = store.submit(first.case_id, [photo(tmp_path, "替换.png", "blue")], user_text="重新拍摄", metadata={}, replace_asset_id=source_id)
        assert record["snapshot"]["memory"] == {}


@pytest.mark.asyncio
async def test_cropped_evidence_pixels_can_be_read_after_restart(tmp_path):
    settings = Settings(state_dir=tmp_path / "state")
    result = await run_customer_case([photo(tmp_path)], user_text="包装破损", goal="帮助售后", metadata={"order_id": "O1"}, settings=settings, policy=Policy(), replay="blurred")
    derived = [e for e in result.evidence if e.asset.parent_asset_id]
    assert derived and derived[0].asset.derived_path
    with CaseStore(settings.state_dir) as store:
        assets, evidence, _ = store.restore(store.get(result.case_id))
        assert assets.data(derived[0].asset.asset_id).startswith(b"\x89PNG")
        assert evidence[-1].asset.transforms[-1]["operation"] == "crop_image"
