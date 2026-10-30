import json

import pytest
from starlette.websockets import WebSocketDisconnect
from test_telephony import (
    VOICEBOT_PASSWORD,
    VOICEBOT_USERNAME,
    WEBHOOK_TOKEN,
    make_client,
    resolve_voicebot_stream_path,
)

from app.api.routes import demo
from app.core.config import Settings, get_settings
from app.workflows.consent import (
    ENGLISH_CONSENT_PROMPT,
    ENGLISH_CONSENT_REPROMPT,
    ENGLISH_CONSENT_UNCLEAR_CLOSURE,
)

CODE = "open-sesame"


@pytest.fixture(autouse=True)
def reset_active_sessions() -> None:
    demo._active_sessions.clear()


def make_demo_client(**overrides):
    client, events, stt_sessions, tts_providers = make_client()
    settings = Settings(
        _env_file=None,
        exotel_webhook_token=WEBHOOK_TOKEN,
        public_base_url="https://demo.example.test",
        exotel_voicebot_username=VOICEBOT_USERNAME,
        exotel_voicebot_password=VOICEBOT_PASSWORD,
        sarvam_api_key="test-sarvam-api-key",
        demo_mode_enabled=True,
        demo_access_code=CODE,
        access_cookie_secret="s" * 32,
        **overrides,
    )
    client.app.dependency_overrides[get_settings] = lambda: settings
    return client, events, stt_sessions, tts_providers


def login(client) -> None:
    assert client.post("/api/v1/access/login", json={"code": CODE}).status_code == 200


def test_demo_session_requires_access_code() -> None:
    client, *_ = make_demo_client()
    with client:
        assert client.post("/api/v1/demo/session").status_code == 401


def test_demo_session_streams_the_same_intake_pipeline() -> None:
    client, events, stt_sessions, tts_providers = make_demo_client()
    with client:
        login(client)
        session = client.post("/api/v1/demo/session").json()
        with client.websocket_connect(session["streamPath"]) as websocket:
            assert websocket.receive_json()["type"] == "transcript"
            assert websocket.receive_bytes()
            assert websocket.receive_json()["type"] == "agent_transcript_done"
            websocket.send_text("stop")

    call = events.intake_calls.documents[0]
    assert call["telephony"]["provider"] == "demo"
    assert call["id"] == session["intakeCallId"]
    assert len(stt_sessions) == 1 and len(tts_providers) == 1
    assert events.documents and {e["provider"] for e in events.documents} == {"demo"}


def test_demo_audio_is_sent_in_hundred_ms_frames(monkeypatch) -> None:
    from app.api.routes import telephony
    from app.providers.contracts import SpeechAudio

    pcm = bytes(range(256)) * 16

    class SplitTts:
        async def stream(self, text, *, language_code=None, speaker=None):
            yield SpeechAudio(content=pcm[:801], content_type="audio/L16")
            yield SpeechAudio(content=pcm[801:], content_type="audio/L16")

    client, *_ = make_demo_client()
    monkeypatch.setattr(telephony, "_create_tts_provider", lambda settings: SplitTts())
    with client:
        login(client)
        session = client.post("/api/v1/demo/session").json()
        assert session["sampleRate"] == 16000
        path = session["streamPath"]
        with client.websocket_connect(path) as websocket:
            assert websocket.receive_json()["type"] == "transcript"
            frames = [websocket.receive_bytes(), websocket.receive_bytes()]
            assert websocket.receive_json()["type"] == "agent_transcript_done"
            websocket.send_text("stop")
    assert [len(frame) for frame in frames] == [3200, 896]
    assert b"".join(frames) == pcm


def test_demo_ignores_microphone_audio_while_agent_speaks_by_default(monkeypatch) -> None:
    import asyncio

    from app.api.routes import telephony
    from app.providers.contracts import SpeechAudio

    class SlowTts:
        async def stream(self, text, *, language_code=None, speaker=None):
            yield SpeechAudio(content=b"ab", content_type="audio/L16")
            await asyncio.sleep(30)

    client, events, stt_sessions, _ = make_demo_client()
    monkeypatch.setattr(telephony, "_create_tts_provider", lambda settings: SlowTts())
    with client:
        login(client)
        path = client.post("/api/v1/demo/session").json()["streamPath"]
        with client.websocket_connect(path) as websocket:
            assert websocket.receive_json()["speaker"] == "agent"
            assert websocket.receive_bytes() == b"ab"
            stt_sessions[0].transcript_on_next_audio = ("transcript.partial", "English", "en-IN")
            websocket.send_bytes(b"caller")
            websocket.send_text("stop")
            assert websocket.receive()["type"] == "websocket.close"
    assert stt_sessions[0].audio_payloads == []
    assert not any(
        event["event_type"] == "voicebot_agent_interrupted" for event in events.documents
    )


def test_demo_can_enable_barge_in_explicitly(monkeypatch) -> None:
    import asyncio

    from app.api.routes import telephony
    from app.providers.contracts import SpeechAudio

    class SlowTts:
        async def stream(self, text, *, language_code=None, speaker=None):
            yield SpeechAudio(content=b"ab", content_type="audio/L16")
            await asyncio.sleep(30)

    client, events, stt_sessions, _ = make_demo_client(demo_barge_in_enabled=True)
    monkeypatch.setattr(telephony, "_create_tts_provider", lambda settings: SlowTts())
    with client:
        login(client)
        path = client.post("/api/v1/demo/session").json()["streamPath"]
        with client.websocket_connect(path) as websocket:
            assert websocket.receive_json()["speaker"] == "agent"
            assert websocket.receive_bytes() == b"ab"
            stt_sessions[0].transcript_on_next_audio = ("transcript.partial", "English", "en-IN")
            websocket.send_bytes(b"caller")
            assert websocket.receive_text() == "clear"
            websocket.send_text("stop")
    assert any(event["event_type"] == "voicebot_agent_interrupted" for event in events.documents)


def test_demo_stream_rejects_missing_access_cookie() -> None:
    client, *_ = make_demo_client()
    with client:
        login(client)
        path = client.post("/api/v1/demo/session").json()["streamPath"]
        client.cookies.clear()
        with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(path):
            pass
    assert error.value.code == 1008


def test_demo_stream_rejects_non_demo_session_tokens() -> None:
    client, *_ = make_demo_client()
    with client:
        login(client)
        exotel_path = resolve_voicebot_stream_path(client, "sid-exotel-call")
        token = exotel_path.split("session_token=")[1]
        with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(
            f"/api/v1/demo/stream?session_token={token}"
        ):
            pass
    assert error.value.code == 1008


def test_demo_stream_limits_one_active_session_per_visitor() -> None:
    client, *_ = make_demo_client()
    with client:
        login(client)
        first = client.post("/api/v1/demo/session").json()["streamPath"]
        second = client.post("/api/v1/demo/session").json()["streamPath"]
        with client.websocket_connect(first) as websocket:
            websocket.receive_json()
            websocket.receive_bytes()
            with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(second):
                pass
            assert error.value.code == 1013
            websocket.send_text("stop")


def test_demo_agent_announces_trial_end(monkeypatch) -> None:
    from app.voice import session

    monkeypatch.setattr(session, "TRIAL_NOTICE_LEAD_SECONDS", 30)
    client, *_ = make_demo_client(demo_session_max_seconds=30)
    with client:
        login(client)
        path = client.post("/api/v1/demo/session").json()["streamPath"]
        with client.websocket_connect(path) as websocket:
            spoken = []
            for _ in range(40):
                message = websocket.receive()
                if message.get("text") and '"transcript"' in message["text"]:
                    spoken.append(message["text"])
                if session.TRIAL_ENDED in "".join(spoken):
                    break
            websocket.send_text("stop")
    assert session.TRIAL_ENDED in "".join(spoken)


def test_silent_caller_is_reprompted_once_then_call_ends() -> None:
    client, *_ = make_demo_client(caller_silence_seconds=1)
    with client:
        login(client)
        path = client.post("/api/v1/demo/session").json()["streamPath"]
        with client.websocket_connect(path) as websocket:
            spoken = []
            while True:
                message = websocket.receive()
                if message["type"] == "websocket.close":
                    break
                if message.get("text") and '"transcript"' in message["text"]:
                    spoken.append(json.loads(message["text"])["text"])
    assert spoken == [
        ENGLISH_CONSENT_PROMPT,
        ENGLISH_CONSENT_REPROMPT,
        ENGLISH_CONSENT_UNCLEAR_CLOSURE,
    ]
