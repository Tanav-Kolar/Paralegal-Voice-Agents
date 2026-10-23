from base64 import b64encode
from dataclasses import dataclass
from typing import Protocol

from fastapi import WebSocket


@dataclass(frozen=True)
class Start:
    stream_sid: str | None


@dataclass(frozen=True)
class Audio:
    # Base64 Linear16 PCM, as the recorder and STT provider consume it today.
    payload: str


@dataclass(frozen=True)
class Stop:
    pass


TransportEvent = Start | Audio | Stop


class Transport(Protocol):
    """What a voice session needs from the wire: caller events in, agent audio out."""

    async def accept(self) -> None: ...

    async def recv(self) -> TransportEvent | None:
        """Next event, or None for a frame the session should ignore."""
        ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def close(self, code: int | None = None) -> None: ...


class ExotelTransport:
    """Exotel Voicebot websocket frames <-> transport events."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._stream_sid: str | None = None

    async def accept(self) -> None:
        await self._websocket.accept()

    async def recv(self) -> TransportEvent | None:
        message = await self._websocket.receive_json()
        event = message.get("event")
        if event == "start":
            self._stream_sid = message.get("start", {}).get("stream_sid")
            return Start(self._stream_sid)
        if event == "media":
            payload = message.get("media", {}).get("payload")
            return Audio(payload) if isinstance(payload, str) and payload else None
        if event == "stop":
            return Stop()
        return None

    async def send_audio(self, pcm: bytes) -> None:
        await self._websocket.send_json(
            {
                "event": "media",
                "stream_sid": self._stream_sid,
                "media": {"payload": b64encode(pcm).decode()},
            }
        )

    async def close(self, code: int | None = None) -> None:
        if code is None:
            await self._websocket.close()
        else:
            await self._websocket.close(code=code)
