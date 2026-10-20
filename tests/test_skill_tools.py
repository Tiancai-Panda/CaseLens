import pytest
from test_agent import setup_case

from caselens.schemas import ToolCall


@pytest.mark.asyncio
async def test_quantity_skill_runs_trusted_check_with_source_evidence(tmp_path):
    service, _, cache, asset = setup_case(tmp_path)
    try:
        await service.analyze_visual(asset.asset_id, "核实数量")
        eid = next(iter(service.evidence))
        args = {"expected": 3, "received": 2, "pending": 1, "order_evidence": True, "complete_photo": True, "evidence_ids": [eid]}
        result = await service.registry().execute(ToolCall(call_id="q1", name="check_quantity", arguments={"data": args}))
        assert result.ok
        assert result.data["status"] == "pending_shipment"
        assert result.data["evidence_ids"] == [eid]
        assert result.data["candidate_counts_not_verified"] is True
        assert "missing-quantity" in [s["name"] for s in service.active_skills]
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_quantity_skill_cannot_use_invented_evidence_ids(tmp_path):
    service, _, cache, _ = setup_case(tmp_path)
    try:
        result = await service.registry().execute(ToolCall(call_id="q", name="check_quantity", arguments={"data": {"expected": 3, "received": 2, "pending": 0, "order_evidence": True, "complete_photo": True, "evidence_ids": ["made-up"]}}))
        assert result.error_code == "invalid_evidence"
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_skill_activation_is_deduplicated_and_resources_are_confined(tmp_path):
    service, _, cache, _ = setup_case(tmp_path)
    try:
        await service.activate_skill("missing-quantity")
        await service.activate_skill("missing-quantity")
        assert len(service.active_skills) == 1
        output = await service.registry().execute(ToolCall(call_id="r", name="read_skill_resource", arguments={"name": "missing-quantity", "path": "../../caselens.json"}))
        assert not output.ok
    finally:
        cache.close()
