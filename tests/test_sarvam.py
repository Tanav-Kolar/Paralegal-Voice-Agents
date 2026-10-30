import json

import pytest

from app.core.config import Settings
from app.providers.sarvam import SarvamRealtimeSpeechToTextSession


class FakeSarvamConnection:
    def __init__(self, events: list[dict]) -> None:
        self.events = [json.dumps(event) for event in events]
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
async def test_sarvam_realtime_stt_relays_audio_and_final_transcript() -> None:
    received: list[tuple[str, str, str | None]] = []
    connection = FakeSarvamConnection(
        [{"event": "transcript.final", "text": "नमस्ते hello", "language": "hi-IN"}]
    )

    async def connect_to_sarvam(url: str, **kwargs) -> FakeSarvamConnection:
        assert "sample_rate=8000" in url
        assert "encoding=linear16" in url
        assert "silence_duration_ms=1800" in url
        assert kwargs["additional_headers"] == {"api-subscription-key": "test-key"}
        return connection

    async def on_transcript(event_type: str, text: str, language: str | None) -> None:
        received.append((event_type, text, language))

    session = SarvamRealtimeSpeechToTextSession(
        Settings(_env_file=None, sarvam_api_key="test-key"),
        on_transcript,
        websocket_connect=connect_to_sarvam,
    )
    await session.start()
    await session.send_audio("cGNtLWF1ZGlv")
    await session.stop()

    assert connection.messages == [
        {"event": "audio_input", "audio": "cGNtLWF1ZGlv"},
        {"event": "end"},
    ]
    assert connection.closed is True
    assert received == [("transcript.final", "नमस्ते hello", "hi-IN")]
