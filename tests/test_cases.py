import asyncio

import pytest
from PIL import Image

from caselens.cases import CaseStore, run_customer_case
from caselens.config import Settings
from caselens.schemas import Policy


def photo(tmp_path, name="照片.png", color="white"):
    path = tmp_path / name
    Image.new("RGB", (100, 80), color).save(path)
    return path


@pytest.mark.asyncio
async def test_resume_same_case_after_restart_and_replace_irrelevant_photo(tmp_path):
    settings = Settings(state_dir=tmp_path / "state")
    first = await run_customer_case([photo(tmp_path)], user_text="包装破了", goal="帮助处理售后", metadata={"order_id": "O-1"}, settings=settings, policy=Policy(), replay="irrelevant")
    assert first.status == "needs_review"
    assert first.case_id
    with CaseStore(settings.state_dir) as store:
        saved = store.get(first.case_id)
        original_id = saved["images"][0]["asset_id"]
        assert len(store.history(first.case_id)) == 1
    second = await run_customer_case([photo(tmp_path, "新照片.png", "blue")], user_text="已重新拍清楚", goal="", metadata={}, settings=settings, policy=None, replay="clear", case_id=first.case_id, replace_asset_id=original_id)
    assert second.case_id == first.case_id
    assert second.status == "completed"
    assert second.submission == 2
    assert original_id not in {e.asset.root_asset_id for e in second.evidence}
    assert second.resolutions
    with CaseStore(settings.state_dir) as store:
        assert len(store.history(first.case_id)) == 2
        assert store.get(first.case_id)["metadata"]["order_id"] == "O-1"


@pytest.mark.asyncio
async def test_text_only_supplement_reuses_evidence_and_does_not_reset_visual_budget(tmp_path):
    settings = Settings(state_dir=tmp_path / "state")
    first = await run_customer_case([photo(tmp_path)], user_text="包装破了", goal="售后", metadata={}, settings=settings, policy=Policy(), replay="clear")
    assert first.status == "needs_review"
    second = await run_customer_case([], user_text="补充订单号", goal="", metadata={"order_id": "O-1"}, settings=settings, policy=None, replay="clear", case_id=first.case_id)
    assert second.status == "completed"
    assert second.evidence[0].evidence_id == first.evidence[0].evidence_id
    assert sum(call.model == "recorded-vlm" for call in second.calls) == 0


def test_invalid_image_and_path_escape_do_not_create_a_case(tmp_path):
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"broken")
    with CaseStore(tmp_path / "state") as store:
        with pytest.raises(ValueError):
            store.create([broken], user_text="坏了", goal="售后", metadata={}, policy=Policy(), mode="replay")
        with pytest.raises(ValueError):
            store.get("../../private")
        assert store.connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0] == 0


def test_exclusive_case_claim_and_noop_submission_are_rejected(tmp_path):
    with CaseStore(tmp_path / "state") as store:
        record = store.create([photo(tmp_path)], user_text="包装破损", goal="售后", metadata={}, policy=Policy(), mode="replay")
        store.claim(record["case_id"])
        with pytest.raises(ValueError, match="running"):
            store.claim(record["case_id"])
        store.release(record["case_id"])
        with pytest.raises(ValueError, match="new"):
            store.submit(record["case_id"], [], user_text="", metadata={})


@pytest.mark.asyncio
async def test_live_replay_mode_is_immutable_and_policy_is_pinned(tmp_path):
    settings = Settings(state_dir=tmp_path / "state")
    first = await run_customer_case([photo(tmp_path)], user_text="包装破损", goal="售后", metadata={}, settings=settings, policy=Policy(), replay="missing")
    with pytest.raises(ValueError, match="mode"):
        await run_customer_case([], user_text="补充", goal="", metadata={}, settings=settings, policy=None, case_id=first.case_id)
    with pytest.raises(ValueError, match="policy"):
        await run_customer_case([], user_text="补充", goal="", metadata={}, settings=settings, policy=Policy(version="changed"), replay="clear", case_id=first.case_id)


@pytest.mark.asyncio
async def test_cancelled_run_releases_claim_and_keeps_submission(tmp_path, monkeypatch):
    from caselens.replay import CustomerReplayJev
    async def cancelled(self, state):
        raise asyncio.CancelledError
    monkeypatch.setattr(CustomerReplayJev, "detect_intent", cancelled)
    settings = Settings(state_dir=tmp_path / "state")
    with pytest.raises(asyncio.CancelledError):
        await run_customer_case([photo(tmp_path)], user_text="包装破损", goal="售后", metadata={}, settings=settings, policy=Policy(), replay="clear")
    with CaseStore(settings.state_dir) as store:
        rows = store.connection.execute("SELECT payload FROM cases").fetchall()
        assert len(rows) == 1
        import json
        record = json.loads(rows[0][0])
        assert record["running"] is False
        assert record["status"] == "partial"


@pytest.mark.asyncio
async def test_new_submission_after_initial_interruption_is_not_lost(tmp_path):
    settings = Settings(state_dir=tmp_path / "state")
    with CaseStore(settings.state_dir) as store:
        record = store.create([photo(tmp_path)], user_text="包装破损", goal="售后", metadata={}, policy=Policy(), mode="replay")
    result = await run_customer_case([], user_text="补充订单编号", goal="", metadata={"order_id": "O-1"}, settings=settings, policy=None, replay="clear", case_id=record["case_id"])
    assert result.submission == 2
    assert result.status == "completed"


def test_submission_limit_and_corrected_text_are_persistent(tmp_path):
    with CaseStore(tmp_path / "state") as store:
        record = store.create([photo(tmp_path)], user_text="包装破损", goal="售后", metadata={}, policy=Policy(), mode="replay")
        record = store.submit(record["case_id"], [], user_text="其实是少发", metadata={}, replace_text=True)
        _, _, text = store.restore(record)
        assert "包装破损" not in text
        assert "其实是少发" in text
        for number in range(6):
            store.submit(record["case_id"], [], user_text=f"补充{number}", metadata={})
        with pytest.raises(ValueError, match="budget"):
            store.submit(record["case_id"], [], user_text="第九次", metadata={})
