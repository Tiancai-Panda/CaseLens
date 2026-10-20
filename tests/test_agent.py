import pytest
from PIL import Image

from caselens.agent import Agent
from caselens.assets import AssetStore
from caselens.config import Settings
from caselens.replay import replay_clients
from caselens.schemas import Policy, ToolCall
from caselens.store import EvidenceStore
from caselens.tools import ToolService


def setup_case(tmp_path, scenario="clear", metadata=None):
    path = tmp_path / "订单照片.png"
    Image.new("RGB", (160, 120), "white").save(path)
    assets = AssetStore(tmp_path)
    asset = assets.register(path)
    vlm, jev, controller = replay_clients(scenario)
    cache = EvidenceStore(tmp_path / "state.sqlite3")
    service = ToolService(assets, vlm, jev, cache, Policy(), metadata if metadata is not None else {"order_id": "DEMO-1"}, Settings())
    return service, controller, cache, asset


@pytest.mark.parametrize("scenario,status", [("clear", "completed"), ("blurred", "completed"), ("missing", "needs_review")])
@pytest.mark.asyncio
async def test_scenarios_follow_different_paths_with_source_citations(tmp_path, scenario, status):
    service, controller, cache, asset = setup_case(tmp_path, scenario, metadata={} if scenario == "missing" else None)
    try:
        result = await Agent(service, controller, mode="replay").run("判断包装是否破损")
    finally:
        cache.close()
    assert result.status == status
    assert result.mode == "replay"
    assert result.evidence[0].asset.root_asset_id == asset.asset_id
    assert result.evidence[0].asset.source_path.endswith("订单照片.png")
    tools = [s.call.name for s in result.steps if s.result.ok]
    assert ("crop_image" in tools) == (scenario == "blurred")
    assert ("request_information" in tools) == (scenario == "missing")
    assert max(s.round for s in result.steps) <= 6


@pytest.mark.asyncio
async def test_early_finish_and_invalid_parameters_are_observations_not_crashes(tmp_path):
    service, _, cache, _ = setup_case(tmp_path)
    try:
        registry = service.registry()
        early = await registry.execute(ToolCall(call_id="c1", name="finish", arguments={"summary": "退款"}))
        invalid = await registry.execute(ToolCall(call_id="c2", name="crop_image", arguments={"asset_id": "no", "region": [1, 1]}))
        missing = await registry.execute(ToolCall(call_id="c3", name="shell", arguments={}))
        assert early.error_code == "evidence_gate"
        assert invalid.error_code == "invalid_arguments"
        assert missing.error_code == "unknown_tool"
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_new_evidence_invalidates_old_decision_and_limits_supplements(tmp_path):
    service, _, cache, asset = setup_case(tmp_path, "blurred")
    try:
        registry = service.registry()
        await registry.execute(ToolCall(call_id="p", name="read_policy", arguments={"policy_id": "default"}))
        first = await registry.execute(ToolCall(call_id="a", name="analyze_visual", arguments={"asset_id": asset.asset_id, "question": "全图"}))
        await registry.execute(ToolCall(call_id="d", name="evaluate_decision", arguments={"evidence_ids": [first.data["evidence_id"]]}))
        assert service.decision is not None
        await registry.execute(ToolCall(call_id="b", name="analyze_visual", arguments={"asset_id": asset.asset_id, "question": "看右下角"}))
        assert service.decision is None
        # Exhaust recorded observations only after the production supplement check.
        service.analysis_counts[asset.root_asset_id] = 3
        limited = await registry.execute(ToolCall(call_id="c", name="analyze_visual", arguments={"asset_id": asset.asset_id, "question": "第四次分析"}))
        assert limited.error_code == "supplement_limit"
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_identical_successful_tool_call_is_not_executed_twice(tmp_path):
    service, _, cache, asset = setup_case(tmp_path)
    try:
        registry = service.registry()
        args = {"asset_id": asset.asset_id, "question": "检查纸箱"}
        first = await registry.execute(ToolCall(call_id="1", name="analyze_visual", arguments=args))
        repeated = await registry.execute(ToolCall(call_id="2", name="analyze_visual", arguments=args))
        assert first.ok
        assert repeated.error_code == "repeated_call"
        assert len(service.evidence) == 1
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_cache_rebinds_source_on_a_new_run(tmp_path):
    service, _, cache, asset = setup_case(tmp_path)
    try:
        first = await service.registry().execute(ToolCall(call_id="1", name="analyze_visual", arguments={"asset_id": asset.asset_id, "question": "检查"}))
        second_path = tmp_path / "另一份.png"
        second_path.write_bytes((tmp_path / "订单照片.png").read_bytes())
        second_asset = service.assets.register(second_path)
        second = await service.registry().execute(ToolCall(call_id="2", name="analyze_visual", arguments={"asset_id": second_asset.asset_id, "question": "检查"}))
        assert second.data["cached"] is True
        assert second.data["asset"]["source_path"].endswith("另一份.png")
        assert second.data["evidence_id"] != first.data["evidence_id"]
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_partially_warm_replay_does_not_shift_crop_response(tmp_path):
    service, _, cache, asset = setup_case(tmp_path, "blurred")
    try:
        await service.analyze_visual(asset.asset_id, "检查包装的可见破损与未知项")
    finally:
        cache.close()
    service, controller, cache, _ = setup_case(tmp_path, "blurred")
    try:
        result = await Agent(service, controller, mode="replay").run("检查")
        assert result.status == "completed"
        assert result.evidence[1].observation.facts == ["局部放大后可见纸箱边缘撕裂"]
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_budget_stops_a_controller_that_never_finishes(tmp_path):
    from caselens.replay import ReplayController
    service, _, cache, _ = setup_case(tmp_path)
    controller = ReplayController([[{"name": "shell", "arguments": {}}]] * 8)
    try:
        result = await Agent(service, controller, mode="replay").run("检查")
        assert result.status == "failed"
        assert len(result.steps) == 6
        assert "budget" in result.errors[-1]["code"]
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_dependent_calls_in_one_round_are_rejected(tmp_path):
    from caselens.replay import ReplayController
    service, _, cache, _ = setup_case(tmp_path)
    controller = ReplayController([[{"name": "analyze_visual", "arguments": {"asset_id": "$asset", "question": "检查"}}, {"name": "evaluate_decision", "arguments": {"evidence_ids": []}}]])
    service.settings.max_rounds = 1
    try:
        result = await Agent(service, controller, mode="replay").run("检查")
        assert all(s.result.error_code == "dependent_calls" for s in result.steps)
        assert len(service.evidence) == 0
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_missing_order_metadata_cannot_be_overridden_by_high_probability(tmp_path):
    service, _, cache, asset = setup_case(tmp_path, metadata={})
    try:
        registry = service.registry()
        await registry.execute(ToolCall(call_id="p", name="read_policy", arguments={"policy_id": "default"}))
        analysis = await registry.execute(ToolCall(call_id="a", name="analyze_visual", arguments={"asset_id": asset.asset_id, "question": "检查"}))
        await registry.execute(ToolCall(call_id="d", name="evaluate_decision", arguments={"evidence_ids": [analysis.data["evidence_id"]]}))
        finish = await registry.execute(ToolCall(call_id="f", name="finish", arguments={"summary": "证据齐全"}))
        assert finish.error_code == "evidence_gate"
        assert service.final_status is None
    finally:
        cache.close()


@pytest.mark.asyncio
async def test_concurrent_runs_do_not_mix_shared_client_records(tmp_path):
    import asyncio
    services = []
    caches = []
    controllers = []
    for name in ["first", "second"]:
        folder = tmp_path / name
        folder.mkdir()
        service, controller, cache, _ = setup_case(folder)
        services.append(service)
        caches.append(cache)
        controllers.append(controller)
    from caselens.replay import SCENARIOS, ReplayJev, ReplayVLM
    vlm = ReplayVLM(SCENARIOS["clear"]["observations"] * 2)
    jev = ReplayJev(SCENARIOS["clear"]["decisions"] * 2)
    for service in services:
        service.vlm, service.jev = vlm, jev
    try:
        results = await asyncio.gather(*(Agent(service, controller, mode="replay").run("检查") for service, controller in zip(services, controllers)))
        for result in results:
            assert sum(call.model == "recorded-vlm" for call in result.calls) == 1
            assert sum(call.model == "recorded-jev" for call in result.calls) == 1
            assert all(call.run_id == result.run_id for call in result.calls)
        assert vlm.records == []
        assert jev.records == []
    finally:
        for cache in caches:
            cache.close()
