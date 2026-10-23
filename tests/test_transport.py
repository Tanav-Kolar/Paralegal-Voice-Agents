from base64 import b64decode

from app.voice.transport import Audio, ExotelTransport, Start, Stop


class FakeWebSocket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.sent = []

    async def receive_json(self):
        return self.frames.pop(0)

    async def send_json(self, message):
        self.sent.append(message)


async def test_exotel_frames_map_to_events_and_unknown_frames_are_ignored():
    ws = FakeWebSocket(
        [
            {"event": "connected"},
            {"event": "start", "start": {"stream_sid": "sid-1"}},
            {"event": "media", "media": {"payload": "AAA="}},
            {"event": "media", "media": {"payload": ""}},
            {"event": "media"},
            {"event": "dtmf"},
            {"event": "stop"},
        ]
    )
    transport = ExotelTransport(ws)

    events = [await transport.recv() for _ in range(7)]

    assert events == [None, Start("sid-1"), Audio("AAA="), None, None, None, Stop()]


async def test_exotel_outbound_audio_carries_stream_sid_and_base64_payload():
    ws = FakeWebSocket([{"event": "start", "start": {"stream_sid": "sid-1"}}])
    transport = ExotelTransport(ws)
    await transport.recv()

    await transport.send_audio(b"\x01\x02\x03")

    frame = ws.sent[0]
    assert frame["event"] == "media"
    assert frame["stream_sid"] == "sid-1"
    assert b64decode(frame["media"]["payload"]) == b"\x01\x02\x03"
