from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState


@dataclass(frozen=True)
class Start:
    stream_sid: str | None


@dataclass(frozen=True)
class Audio:
    pcm: bytes  # Linear16 mono at the configured STT sample rate


@dataclass(frozen=True)
class Stop:
    pass


TransportEvent = Start | Audio | Stop


def _require_open(websocket: WebSocket) -> None:
    # After we close from a callback, receiving would raise a bare RuntimeError; end the loop
    # through the normal disconnect path instead.
    if websocket.application_state != WebSocketState.CONNECTED:
        raise WebSocketDisconnect(1000)


class Transport(Protocol):
    """What a voice session needs from the wire: caller events in, agent audio out."""

    provider: str

    async def accept(self) -> None: ...

    async def recv(self) -> TransportEvent | None:
        """Next event, or None for a frame the session should ignore."""
        ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def clear(self) -> None:
        """Drop agent audio the far end has buffered but not yet played."""
        ...

    async def close(self, code: int | None = None) -> None: ...


class ExotelTransport:
    """Exotel Voicebot websocket frames <-> transport events."""

    provider = "exotel"

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._stream_sid: str | None = None

    async def accept(self) -> None:
        await self._websocket.accept()

    async def recv(self) -> TransportEvent | None:
        _require_open(self._websocket)
        message = await self._websocket.receive_json()
        event = message.get("event")
        if event == "start":
            self._stream_sid = message.get("start", {}).get("stream_sid")
            return Start(self._stream_sid)
        if event == "media":
            payload = message.get("media", {}).get("payload")
            if not isinstance(payload, str) or not payload:
                return None
            try:
                return Audio(b64decode(payload, validate=True))
            except BinasciiError:
                return None
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

    async def clear(self) -> None:
        await self._websocket.send_json({"event": "clear", "stream_sid": self._stream_sid})

    async def close(self, code: int | None = None) -> None:
        if code is None:
            await self._websocket.close()
        else:
            await self._websocket.close(code=code)


class BrowserTransport:
    """Browser demo: binary websocket frames are raw PCM both ways; the text "stop" hangs up."""

    provider = "demo"

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._stream_sid = f"demo-{uuid4().hex}"
        self._started = False

    async def accept(self) -> None:
        await self._websocket.accept()

    async def recv(self) -> TransportEvent | None:
        if not self._started:
            self._started = True
            return Start(self._stream_sid)
        _require_open(self._websocket)
        message = await self._websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(message.get("code", 1000))
        if message.get("bytes"):
            return Audio(message["bytes"])
        return Stop() if message.get("text") == "stop" else None

    async def send_audio(self, pcm: bytes) -> None:
        await self._websocket.send_bytes(pcm)

    async def clear(self) -> None:
        await self._websocket.send_text("clear")

    async def close(self, code: int | None = None) -> None:
        if code is None:
            await self._websocket.close()
        else:
            await self._websocket.close(code=code)
