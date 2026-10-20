import json

import httpx
import pytest

from caselens.clients import ControllerClient
from caselens.config import EndpointConfig


@pytest.mark.asyncio
async def test_controller_request_uses_actual_reserved_output_limit():
    def serve(request):
        body = json.loads(request.content)
        assert body["max_tokens"] == 777
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "request_information", "arguments": '{"fields":["photo"]}'}}]}}]})
    async with ControllerClient(EndpointConfig(base_url="https://model.test/v1", model="test", api_key="test"), transport=httpx.MockTransport(serve)) as client:
        client.output_token_limit = 777
        result = await client.next_action([], tool_schemas=[])
    assert result.calls[0].name == "request_information"
