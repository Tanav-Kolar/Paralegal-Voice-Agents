import json
from base64 import b64encode

import pytest

from app.core.config import Settings
from app.providers.sarvam import SarvamStreamingTextToSpeech


class FakeSarvamTtsConnection:
    def __init__(self) -> None:
        self.events = [
            json.dumps(
                {
                    "type": "audio",
                    "data": {"audio": b64encode(b"audio-chunk").decode()},
                }
            ),
            json.dumps({"type": "event", "data": {"event_type": "final"}}),
        ]
        self.messages: list[dict] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.messages.append(json.loads(message))

    async def close(self) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self.events:
            raise StopAsyncIteration
        return self.events.pop(0)


@pytest.mark.asyncio
async def test_sarvam_streaming_tts_returns_linear16_audio_chunks() -> None:
    connection = FakeSarvamTtsConnection()

    async def connect_to_sarvam(url: str, **kwargs) -> FakeSarvamTtsConnection:
        assert "model=bulbul%3Av4-flash" in url
        assert kwargs["additional_headers"] == {"api-subscription-key": "test-key"}
        return connection

    provider = SarvamStreamingTextToSpeech(
        Settings(_env_file=None, sarvam_api_key="test-key"),
        websocket_connect=connect_to_sarvam,
    )
    audio = [chunk async for chunk in provider.stream("Hello")]

    assert [chunk.content for chunk in audio] == [b"audio-chunk"]
    assert connection.messages == [
        {
            "type": "config",
            "data": {
                "speaker": "aparna_en_companion",
                "language_code": "en-IN",
                "speech_sample_rate": 8000,
                "output_audio_codec": "linear16",
                "pace": 1.0,
            },
        },
        {"type": "text", "data": {"text": "Hello"}},
        {"type": "flush"},
    ]
    assert connection.closed is True
