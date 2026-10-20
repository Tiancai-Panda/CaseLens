import json

import httpx
import pytest

from caselens.clients import ControllerClient, VLMClient
from caselens.config import EndpointConfig, JevConfig, Settings
from caselens.schemas import Policy
from caselens.service import Runtime, fingerprint


@pytest.mark.parametrize("role", ["vlm", "controller", "jev"])
def test_inline_key_takes_precedence_and_is_not_serialized(role, monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "env-key")
    settings = Settings.model_validate({role: {"api_key": "  sk-test-inline  "}})
    endpoint = getattr(settings, role)
    assert endpoint.key() == "sk-test-inline"
    assert "sk-test-inline" not in repr(settings)
    assert "api_key" not in endpoint.model_dump()
    assert "sk-test-inline" not in settings.model_dump_json()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_inline_key_falls_back_to_environment(value, monkeypatch):
    monkeypatch.setenv("VLM_API_KEY", "env-key")
    assert EndpointConfig(api_key=value).key() == "env-key"


def test_no_key_gives_actionable_error_without_secret(monkeypatch):
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="api_key"):
        EndpointConfig().key()


def test_endpoint_environment_override_preserves_inline_credential(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"vlm": {"api_key": "sk-test-inline", "model": "original"}}), encoding="utf-8")
    monkeypatch.setenv("CASELENS_VLM_MODEL", "override")
    settings = Settings.load(config)
    assert settings.vlm.model == "override"
    assert settings.vlm.key() == "sk-test-inline"


def test_key_rotation_does_not_change_business_resume_fingerprint():
    settings = Settings(vlm=EndpointConfig(api_key="first-key"))
    first = fingerprint(settings, Policy(), "inspect", {}, "live")
    settings.vlm = EndpointConfig(api_key="second-key")
    assert fingerprint(settings, Policy(), "inspect", {}, "live") == first


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [VLMClient, ControllerClient])
async def test_chat_adapter_sends_inline_key_without_environment(role, monkeypatch):
    monkeypatch.delenv("VLM_API_KEY", raising=False)
    def serve(request):
        assert request.headers["Authorization"] == "Bearer sk-test-inline"
        if role is VLMClient:
            message = {"content": '{"facts":["box"],"text":[],"unknowns":[]}'}
        else:
            message = {"tool_calls": [{"id": "call1", "type": "function", "function": {"name": "read_policy", "arguments": '{"policy_id":"default"}'}}]}
        return httpx.Response(200, json={"choices": [{"message": message}]})
    async with role(EndpointConfig(base_url="https://provider.test/v1", model="m", api_key="sk-test-inline"), transport=httpx.MockTransport(serve)) as client:
        if role is VLMClient:
            assert (await client.analyze(b"i", "image/png", question="inspect")).facts == ["box"]
        else:
            assert (await client.next_action([], tool_schemas=[])).calls[0].name == "read_policy"


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [True, False])
async def test_provider_gate_uses_resolved_keys_not_variable_names(same_key):
    settings = Settings(
        vlm=EndpointConfig(base_url="https://provider.test/v1", model="v", api_key="first-key"),
        controller=EndpointConfig(base_url="https://provider.test/v1", model="c", api_key="first-key" if same_key else "second-key", api_key_env="CONTROLLER_API_KEY"),
        jev=JevConfig(api_key="jev-key"),
    )
    async with Runtime(settings) as runtime:
        assert (runtime.vlm.executor.gate is runtime.controller.executor.gate) is same_key
