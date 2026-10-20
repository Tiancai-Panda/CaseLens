import pytest
from PIL import Image
from test_agent import setup_case

from caselens.agent import Agent
from caselens.schemas import ToolCall


@pytest.mark.asyncio
async def test_evidence_reader_can_page_inside_a_long_ocr_entry(tmp_path):
    service, _, cache, asset = setup_case(tmp_path)
    try:
        await service.analyze_visual(asset.asset_id, "识别")
        evidence = next(iter(service.evidence.values()))
        evidence.observation.text = ["a" * 2000 + "第二页关键信息"]
        output = await service.registry().execute(ToolCall(call_id="page", name="read_evidence", arguments={"evidence_id": evidence.evidence_id, "field": "text", "offset": 0, "limit": 1, "char_offset": 2000}))
        assert output.ok
        assert output.data["values"] == ["第二页关键信息"]
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_source_mutation_invalidates_even_cached_derived_pixels(tmp_path):
    service, controller, cache, asset = setup_case(tmp_path, "blurred")
    try:
        result = await Agent(service, controller, mode="replay").run("检查")
        derived = result.evidence[-1].asset
        Image.new("RGB", (160, 120), "blue").save(asset.source_path)
        with pytest.raises(ValueError, match="changed"):
            service.assets.data(derived.asset_id)
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_mcp_start_failure_returns_typed_result_and_skips_controller(tmp_path, monkeypatch):
    from caselens.image_mcp import ImageMCPClient
    service, controller, cache, _ = setup_case(tmp_path)
    async def unavailable(self):
        raise RuntimeError("stdio unavailable")
    monkeypatch.setattr(ImageMCPClient, "__aenter__", unavailable)
    try:
        result = await Agent(service, controller, mode="replay").run("检查")
        assert result.status == "failed"
        assert result.errors[0]["code"] == "mcp_unavailable"
        assert not result.steps
    finally:
        cache.close()
