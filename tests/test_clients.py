import asyncio
import json

import httpx
import httpx2
import pytest

from caselens.clients import ControllerClient, JevClient, VLMClient
from caselens.config import EndpointConfig, JevConfig, Limits, Settings
from caselens.errors import ClientError
from caselens.resilience import Executor
from caselens.schemas import Policy, Usage


@pytest.mark.asyncio
async def test_vlm_sends_image_and_strict_schema_and_validates_observation(monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "private-key")
    def serve(request):
        body = json.loads(request.content)
        assert body["messages"][1]["content"][1]["image_url"]["url"] == "data:image/png;base64,aW1hZ2U="
        assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"facts":["torn box"],"text":[],"unknowns":[]}'}}], "usage": {"prompt_tokens": 12, "completion_tokens": 4}})
    client = VLMClient(EndpointConfig(base_url="https://vlm.test/v1", model="vision"), transport=httpx.MockTransport(serve))
    async with client:
        result = await client.analyze(b"image", "image/png", question="inspect")
    assert result.facts == ["torn box"]
    assert client.records[0].usage.input_tokens == 12


@pytest.mark.asyncio
async def test_controller_uses_native_tool_calls_not_content_json(monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "secret")
    def serve(request):
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "read_policy"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "read_policy", "arguments": '{"policy_id":"default"}'}}]}}]})
    async with ControllerClient(EndpointConfig(base_url="https://vlm.test/v1", model="vision"), transport=httpx.MockTransport(serve)) as client:
        result = await client.next_action([], tool_schemas=[{"type": "function", "function": {"name": "read_policy"}}])
    assert result.calls[0].name == "read_policy"
    assert result.calls[0].arguments == {"policy_id": "default"}


@pytest.mark.asyncio
@pytest.mark.parametrize("inline", [False, True])
async def test_jev_uses_official_sdk_wire_shape_and_keeps_confidence(monkeypatch, inline):
    if inline:
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    def serve(request):
        body = json.loads(request.content)
        assert body["state"]["facts"] == ["box torn"]
        assert body["questions"]["category"]["type"] == "choice"
        return httpx2.Response(200, json={"model": "jev-test", "usage": {"input_tokens": 23, "output_tokens": 0}, "answers": {
            "category": {"type": "choice", "choice": "packaging_issue", "probabilities": {"packaging_issue": .9, "product_issue": .05, "insufficient_information": .05}, "confidence": .73},
            "sufficiency": {"type": "choice", "choice": "sufficient", "probabilities": {"sufficient": .9, "needs_visual_review": .05, "needs_user_information": .05}, "confidence": .81}}})
    config = JevConfig(base_url="https://jev.test", model="jev-test", **({"api_key": "secret"} if inline else {}))
    async with JevClient(config, transport=httpx2.MockTransport(serve)) as client:
        result = await client.classify({"facts": ["box torn"]}, policy=Policy(), evidence_ids=["e1"])
    assert result.category.choice == "packaging_issue"
    assert result.category.confidence == .73
    assert result.category.probabilities["packaging_issue"] == .9
    assert result.evidence_ids == ["e1"]


@pytest.mark.asyncio
async def test_429_retries_but_auth_failure_does_not(monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "private-key")
    calls = []
    def serve(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(401, text="private-key token and sensitive provider body")
    async with VLMClient(EndpointConfig(base_url="https://vlm.test/v1", model="v", limits=Limits(backoff_seconds=0)), transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(ClientError) as error:
            await client.analyze(b"i", "image/png", question="inspect")
    assert error.value.code == "authentication"
    assert not error.value.retryable
    assert len(calls) == 2
    assert "private-key" not in str(error.value)
    assert client.records[0].attempts == 2


@pytest.mark.asyncio
async def test_sdk_retries_disabled_and_exhaustion_has_three_attempts(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    attempts = []
    def serve(request):
        attempts.append(request)
        return httpx2.Response(503, json={"error": "offline"})
    async with JevClient(JevConfig(limits=Limits(backoff_seconds=0)), transport=httpx2.MockTransport(serve)) as client:
        with pytest.raises(ClientError):
            await client.classify({}, policy=Policy(), evidence_ids=["e1"])
    assert len(attempts) == 3
    assert client.records[0].attempts == 3


@pytest.mark.asyncio
async def test_executor_limits_inflight_calls():
    executor = Executor("test", "m", Limits(concurrency=2, requests_per_minute=0))
    inflight = 0
    peak = 0
    async def operation():
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(.01)
        inflight -= 1
        return "ok", Usage()
    results = await asyncio.gather(*(executor.run(operation) for _ in range(8)))
    assert results == ["ok"] * 8
    assert peak == 2


@pytest.mark.asyncio
async def test_vlm_and_controller_share_provider_admission(monkeypatch):
    from caselens.resilience import AdmissionGate
    limits = Limits(concurrency=1, requests_per_minute=0)
    gate = AdmissionGate(limits)
    first = Executor("vlm", "m", limits, gate=gate)
    second = Executor("controller", "m", limits, gate=gate)
    active = 0
    peak = 0
    async def operation():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        active -= 1
        return True, Usage()
    await asyncio.gather(first.run(operation), second.run(operation))
    assert peak == 1


@pytest.mark.asyncio
async def test_timeout_and_open_circuit_are_typed():
    executor = Executor("test", "m", Limits(timeout_seconds=.001, max_attempts=1, circuit_failures=1))
    async def hangs():
        await asyncio.sleep(1)
        return None, Usage()
    with pytest.raises(ClientError, match="timeout"):
        await executor.run(hangs)
    with pytest.raises(ClientError) as error:
        await executor.run(hangs)
    assert error.value.code == "circuit_open"


def test_config_rejects_unknown_fields_and_missing_live_settings(tmp_path):
    with pytest.raises(ValueError):
        Settings.model_validate({"vlm": {"unsupported": "secret"}})
    with pytest.raises(ValueError, match="base_url"):
        Settings().validate_live()


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [None, []])
@pytest.mark.parametrize("role,code", [(VLMClient, "invalid_response"), (ControllerClient, "invalid_tool_calls")])
async def test_malformed_provider_message_is_a_recorded_typed_error(monkeypatch, message, role, code):
    monkeypatch.setenv("VLM_API_KEY", "secret")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [{"message": message}]}))
    async with role(EndpointConfig(base_url="https://vlm.test/v1", model="v"), transport=transport) as client:
        with pytest.raises(ClientError) as error:
            if role is VLMClient:
                await client.analyze(b"i", "image/png", question="inspect")
            else:
                await client.next_action([], tool_schemas=[])
    assert error.value.code == code
    assert client.records[0].error_code == code


@pytest.mark.asyncio
async def test_malformed_provider_usage_is_a_typed_error(monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "secret")
    payload = {"choices": [{"message": {"content": '{"facts":["box"],"text":[],"unknowns":[]}'}}], "usage": [1]}
    async with VLMClient(EndpointConfig(base_url="https://vlm.test/v1", model="v"), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
        with pytest.raises(ClientError) as error:
            await client.analyze(b"i", "image/png", question="inspect")
    assert error.value.code == "invalid_response"


@pytest.mark.asyncio
async def test_runtime_shared_circuit_preserves_stricter_provider_settings(monkeypatch):
    from caselens.service import Runtime
    monkeypatch.setenv("VLM_API_KEY", "secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret")
    vlm = EndpointConfig(base_url="https://vlm.test/v1", model="v", limits=Limits(circuit_failures=1, circuit_cooldown_seconds=123))
    controller = EndpointConfig(base_url="https://vlm.test/v1", model="c", limits=Limits(circuit_failures=3, circuit_cooldown_seconds=80))
    async with Runtime(Settings(vlm=vlm, controller=controller)) as runtime:
        async def unavailable():
            raise ClientError("connection", "offline", retryable=True)
        runtime.vlm.executor.limits.max_attempts = 1
        with pytest.raises(ClientError):
            await runtime.vlm.executor.run(unavailable)
        with pytest.raises(ClientError) as error:
            await runtime.controller.executor.run(unavailable)
        assert error.value.code == "circuit_open"
        assert runtime.controller.executor.gate.limits.circuit_cooldown_seconds == 123
