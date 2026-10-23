from base64 import b64decode

import pytest
from fastapi import WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.voice.transport import Audio, BrowserTransport, ExotelTransport, Start, Stop


class FakeWebSocket:
    application_state = WebSocketState.CONNECTED

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    async def receive_json(self):
        return self.frames.pop(0)

    async def receive(self):
        return self.frames.pop(0)

    async def send_bytes(self, data):
        self.sent.append(data)

    async def send_json(self, message):
        self.sent.append(message)


async def test_exotel_frames_map_to_events_and_unknown_frames_are_ignored():
    ws = FakeWebSocket(
        [
            {"event": "connected"},
            {"event": "start", "start": {"stream_sid": "sid-1"}},
            {"event": "media", "media": {"payload": "AAA="}},
            {"event": "media", "media": {"payload": "not-base64"}},
            {"event": "media", "media": {"payload": ""}},
            {"event": "media"},
            {"event": "dtmf"},
            {"event": "stop"},
        ]
    )
    transport = ExotelTransport(ws)

    events = [await transport.recv() for _ in range(8)]

    assert events == [None, Start("sid-1"), Audio(b"\x00\x00"), None, None, None, None, Stop()]


async def test_exotel_outbound_audio_carries_stream_sid_and_base64_payload():
    ws = FakeWebSocket([{"event": "start", "start": {"stream_sid": "sid-1"}}])
    transport = ExotelTransport(ws)
    await transport.recv()

    await transport.send_audio(b"\x01\x02\x03")

    frame = ws.sent[0]
    assert frame["event"] == "media"
    assert frame["stream_sid"] == "sid-1"
    assert b64decode(frame["media"]["payload"]) == b"\x01\x02\x03"


async def test_browser_transport_starts_itself_and_maps_raw_frames():
    ws = FakeWebSocket(
        [
            {"type": "websocket.receive", "bytes": b"\x01\x02"},
            {"type": "websocket.receive", "text": "hello"},
            {"type": "websocket.receive", "text": "stop"},
            {"type": "websocket.disconnect", "code": 1001},
        ]
    )
    transport = BrowserTransport(ws)

    first = await transport.recv()
    assert isinstance(first, Start) and first.stream_sid.startswith("demo-")
    assert await transport.recv() == Audio(b"\x01\x02")
    assert await transport.recv() is None
    assert await transport.recv() == Stop()
    with pytest.raises(WebSocketDisconnect) as error:
        await transport.recv()
    assert error.value.code == 1001

    await transport.send_audio(b"\x09")
    assert ws.sent == [b"\x09"]


async def test_recv_after_server_close_raises_a_clean_disconnect():
    ws = FakeWebSocket([])
    ws.application_state = WebSocketState.DISCONNECTED

    for transport in (ExotelTransport(ws), BrowserTransport(ws)):
        transport._started = True  # skip the browser's synthetic Start
        with pytest.raises(WebSocketDisconnect):
            await transport.recv()
