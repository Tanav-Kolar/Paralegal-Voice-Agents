import pytest
from starlette.websockets import WebSocketDisconnect
from test_telephony import (
    WEBHOOK_TOKEN,
    VOICEBOT_PASSWORD,
    VOICEBOT_USERNAME,
    make_client,
    resolve_voicebot_stream_path,
)

from app.api.routes import demo
from app.core.config import Settings, get_settings

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
            websocket.send_json({"event": "start", "start": {"stream_sid": "demo-stream-1"}})
            assert websocket.receive_json()["event"] == "media"
            websocket.receive_json()
            websocket.send_json({"event": "stop"})

    call = events.intake_calls.documents[0]
    assert call["telephony"]["provider"] == "demo"
    assert call["id"] == session["intakeCallId"]
    assert len(stt_sessions) == 1 and len(tts_providers) == 2


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
            websocket.send_json({"event": "start", "start": {"stream_sid": "a"}})
            websocket.receive_json()
            with pytest.raises(WebSocketDisconnect) as error, client.websocket_connect(second):
                pass
            assert error.value.code == 1013
            websocket.send_json({"event": "stop"})
