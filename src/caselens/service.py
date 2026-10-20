"""Application entry point shared by CLI, batch and future HTTP/MCP adapters."""

import hashlib
import json
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path

import typesafe_sdk as ts

from caselens.agent import Agent
from caselens.assets import AssetStore
from caselens.clients import ControllerClient, JevClient, VLMClient
from caselens.config import Limits, Settings
from caselens.prompts import CONTROL_VERSION, CUSTOMER_VERSION, VISUAL_VERSION
from caselens.replay import replay_clients
from caselens.resilience import AdmissionGate
from caselens.schemas import Policy, RunResult, StepRecord, ToolCall
from caselens.store import EvidenceStore
from caselens.tools import ToolService
from caselens.tracing import CURRENT_CALLS, CURRENT_RUN


class Runtime:
    def __init__(self, settings: Settings, strategy="inspect"):
        self.settings, self.strategy = settings, strategy
        self.stack = AsyncExitStack()
        self.vlm = self.jev = self.controller = None

    async def __aenter__(self):
        settings = self.settings
        endpoint = settings.vlm
        if not endpoint.base_url or not endpoint.model:
            raise ValueError("vlm.base_url and vlm.model must be configured")
        endpoint.key()
        if self.strategy != "analyze":
            settings.jev.key()
        controller_config = settings.controller or settings.vlm
        if self.strategy == "inspect":
            if not controller_config.base_url or not controller_config.model:
                raise ValueError("controller.base_url and controller.model must be configured")
            controller_config.key()
        try:
            self.vlm = await self.stack.enter_async_context(VLMClient(endpoint))
            if self.strategy != "analyze":
                self.jev = await self.stack.enter_async_context(JevClient(settings.jev))
            if self.strategy == "inspect":
                self.controller = await self.stack.enter_async_context(ControllerClient(controller_config))
                self.controller.output_token_limit = settings.context_output_reserve
                if (controller_config.base_url, controller_config.key()) == (endpoint.base_url, endpoint.key()):
                    rates = [v for v in [endpoint.limits.requests_per_minute, controller_config.limits.requests_per_minute] if v]
                    limits = Limits(concurrency=min(endpoint.limits.concurrency, controller_config.limits.concurrency),
                                    requests_per_minute=min(rates) if rates else 0,
                                    circuit_failures=min(endpoint.limits.circuit_failures, controller_config.limits.circuit_failures),
                                    circuit_cooldown_seconds=max(endpoint.limits.circuit_cooldown_seconds, controller_config.limits.circuit_cooldown_seconds))
                    gate = AdmissionGate(limits)
                    self.vlm.executor.gate = self.controller.executor.gate = gate
            return self
        except BaseException as exc:
            await self.stack.aclose()
            if isinstance(exc, ts.TypeSafeError):
                raise ValueError("Jev SDK configuration was rejected") from exc
            raise

    async def __aexit__(self, *args):
        await self.stack.aclose()


def load_policy(path: Path | None) -> Policy:
    return Policy.model_validate_json(path.read_text(encoding="utf-8")) if path else Policy()


def fingerprint(settings: Settings, policy: Policy, goal: str, metadata: dict, mode: str, strategy="inspect") -> str:
    payload = {"settings": settings.model_dump(mode="json"), "policy": policy.model_dump(),
               "goal": goal, "metadata": metadata, "mode": mode, "strategy": strategy,
               "visual_prompt": VISUAL_VERSION, "controller_prompt": CONTROL_VERSION, "customer_prompt": CUSTOMER_VERSION, "app_version": "0.1.0"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def run_case(paths: list[Path], *, goal: str, settings: Settings, policy: Policy,
                   metadata: dict, replay: str | None = None, strategy="inspect", runtime: Runtime | None = None, user_text=""):
    if strategy == "inspect":
        from caselens.cases import run_customer_case
        return await run_customer_case(paths, user_text=user_text, goal=goal, settings=settings, policy=policy,
                                       metadata=metadata, replay=replay, runtime=runtime)
    if not paths or len(paths) > 8:
        raise ValueError("provide between one and eight images")
    root = Path(os.path.commonpath([str(p.resolve().parent) for p in paths]))
    assets = AssetStore(root)
    for path in paths:
        assets.register(path)
    if replay:
        if len(paths) != 1:
            raise ValueError("recorded replay scenarios require exactly one image")
        clients = replay_clients(replay)
        return await _run(assets, clients, goal, settings, policy, metadata, "replay", strategy)
    if runtime:
        return await _run(assets, (runtime.vlm, runtime.jev, runtime.controller), goal, settings, policy, metadata, "live", strategy)
    async with Runtime(settings, strategy) as owned:
        return await _run(assets, (owned.vlm, owned.jev, owned.controller), goal, settings, policy, metadata, "live", strategy)


async def _run(assets, clients, goal, settings, policy, metadata, mode, strategy):
    vlm, jev, controller = clients
    with EvidenceStore(settings.state_dir / "evidence.sqlite3") as cache:
        service = ToolService(assets, vlm, jev, cache, policy, metadata, settings)
        if strategy == "inspect":
            return await Agent(service, controller, mode=mode).run(goal)
        started = time.monotonic()
        result = RunResult(mode=mode, status="failed", goal=goal)
        token = CURRENT_RUN.set(result.run_id)
        calls_token = CURRENT_CALLS.set([])
        try:
            registry = service.registry()
            calls = []
            if strategy == "classify":
                calls.append(ToolCall(call_id="policy", name="read_policy", arguments={"policy_id": policy.policy_id}))
            calls += [ToolCall(call_id=f"analysis-{index}", name="analyze_visual", arguments={"asset_id": asset.asset_id, "question": goal})
                      for index, asset in enumerate(assets.assets.values())]
            for call in calls:
                output = await registry.execute(call)
                result.steps.append(StepRecord(round=1, call=call, result=output))
                if not output.ok:
                    result.errors.append({"code": output.error_code, "message": output.message})
            if strategy == "classify" and not result.errors:
                call = ToolCall(call_id="decision", name="evaluate_decision", arguments={"evidence_ids": list(service.evidence)})
                output = await registry.execute(call)
                result.steps.append(StepRecord(round=2, call=call, result=output))
                if not output.ok:
                    result.errors.append({"code": output.error_code, "message": output.message})
            result.status = ("partial" if service.evidence else "failed") if result.errors else "completed"
            # classify is an explicit pipeline, not accepted agent finalization or an automated action.
            result.summary = "仅提取证据。" if strategy == "analyze" else "固定分类流程结果；不代表工单已获准自动处理。"
            result.evidence, result.decision = list(service.evidence.values()), service.decision
            result.calls = list(CURRENT_CALLS.get() or [])
            result.latency_ms = (time.monotonic()-started)*1000
            return result
        finally:
            CURRENT_CALLS.reset(calls_token)
            CURRENT_RUN.reset(token)
