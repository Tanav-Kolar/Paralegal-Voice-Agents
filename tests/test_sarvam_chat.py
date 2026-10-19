import json

import httpx
import pytest

from app.core.config import Settings
from app.providers.sarvam_chat import SarvamConversationProvider
from app.workflows.conversation import ConversationUnavailable


@pytest.mark.asyncio
async def test_sarvam_chat_sends_playbook_and_validates_structured_response(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"reply": "Tell me more.", "finished": False})
            }}]},
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.providers.sarvam_chat.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    provider = SarvamConversationProvider(Settings(_env_file=None, sarvam_api_key="test-key"))
    result = await provider.reply([{"role": "user", "content": "hello"}])

    assert result.reply == "Tell me more."
    request = requests[0]
    assert request.headers["api-subscription-key"] == "test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "sarvam-105b-conversations"
    assert payload["response_format"]["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_sarvam_chat_rejects_malformed_response(monkeypatch):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": []}))
    monkeypatch.setattr(
        "app.providers.sarvam_chat.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    provider = SarvamConversationProvider(Settings(_env_file=None, sarvam_api_key="test-key"))
    with pytest.raises(ConversationUnavailable):
        await provider.reply([{"role": "user", "content": "hello"}])
