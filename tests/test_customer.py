import pytest
from PIL import Image

from caselens.agent import Agent
from caselens.assets import AssetStore
from caselens.config import Settings
from caselens.customer import CustomerService
from caselens.replay import customer_replay_clients
from caselens.schemas import Policy, ToolCall
from caselens.store import EvidenceStore


def setup(tmp_path, scenario="clear", text="包装破损", metadata=None):
    path = tmp_path / "材料.png"
    Image.new("RGB", (100, 100), "white").save(path)
    assets = AssetStore(tmp_path)
    assets.register(path)
    clients = customer_replay_clients(scenario)
    cache = EvidenceStore(tmp_path / "evidence.sqlite3")
    service = CustomerService(assets, clients[0], clients[1], cache, Policy(), metadata if metadata is not None else {"order_id": "ORDER-1"}, Settings(), user_text=text)
    return service, clients[2], cache


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,status", [("clear", "completed"), ("blurred", "completed"), ("irrelevant", "needs_review"), ("quantity", "completed"), ("multiple", "completed")])
async def test_customer_routes_produce_guidance_or_policy_recommendations(tmp_path, scenario, status):
    service, controller, cache = setup(tmp_path, scenario)
    try:
        result = await Agent(service, controller, mode="replay").run("解决售后问题")
        assert result.status == status
        assert result.intent is not None
        assert result.user_message
        if scenario == "irrelevant":
            assert "重新" in result.user_message
            assert result.supplement.reason == "photo_irrelevant"
            assert result.resolutions == []
        else:
            assert result.resolutions
            assert all(not resolution.executed for resolution in result.resolutions)
        if scenario == "multiple":
            assert {finding.issue for finding in result.findings} == {"packaging_issue", "missing_quantity"}
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_legal_tools_block_early_finish_and_inconsistent_sufficiency(tmp_path):
    service, _, cache = setup(tmp_path, "irrelevant")
    try:
        await service.bootstrap()
        early = await service.registry().execute(ToolCall(call_id="early", name="finish", arguments={"summary": "退款"}))
        assert early.ok is False
        await service.read_policy("default")
        asset = next(iter(service.assets.assets))
        await service.analyze_visual(asset, service.visual_question())
        await service.evaluate_issues(list(service.evidence))
        assert "finish" not in service.allowed_tools()
        finish = await service.registry().execute(ToolCall(call_id="finish", name="finish", arguments={"summary": "结束"}))
        assert finish.ok is False
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_quantity_requires_order_and_item_material_even_with_high_model_probability(tmp_path):
    service, _, cache = setup(tmp_path, "quantity")
    try:
        await service.bootstrap()
        await service.read_policy("default")
        asset = next(iter(service.assets.assets))
        await service.analyze_visual(asset, service.visual_question())
        await service.evaluate_issues(list(service.evidence))
        service.findings[0].supporting_materials = {}
        assert "finish" not in service.allowed_tools()
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_new_visual_evidence_invalidates_findings_and_recommendations(tmp_path):
    service, _, cache = setup(tmp_path, "blurred")
    try:
        await service.bootstrap()
        await service.read_policy("default")
        asset = next(iter(service.assets.assets))
        await service.analyze_visual(asset, "全图")
        await service.evaluate_issues(list(service.evidence))
        assert service.findings
        await service.analyze_visual(asset, "局部")
        assert service.findings == []
        assert service.resolutions == []
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_low_probability_remedy_is_effectively_human_review(tmp_path):
    from caselens.schemas import ChoicePrediction, Resolution
    service, _, cache = setup(tmp_path)
    try:
        await service.bootstrap()
        await service.read_policy("default")
        asset = next(iter(service.assets.assets))
        await service.analyze_visual(asset, "包装")
        await service.evaluate_issues(list(service.evidence))
        async def uncertain(state, *, issue, policy, allowed_actions, evidence_ids):
            return Resolution(issue=issue, action=ChoicePrediction(choice="compensate", probabilities={"compensate": .55, "human_review": .45}), evidence_ids=evidence_ids, policy_version=policy.version, model="recorded")
        service.jev.recommend = uncertain
        await service.finish("结束")
        assert service.final_status == "needs_review"
        assert service.resolutions[0].effective_action == "human_review"
        assert "建议补偿" not in service.user_message
    finally:
        cache.close()
