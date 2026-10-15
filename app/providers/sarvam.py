"""Sarvam adapters used by the voice transport layer."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.core.config import Settings
from app.providers.contracts import TranscriptHandler

logger = logging.getLogger(__name__)

SARVAM_REALTIME_STT_URL = "wss://api.sarvam.ai/speech-to-text-realtime/ws"


class SarvamRealtimeSpeechToTextSession:
    """Relays Exotel Linear16 media frames to Sarvam's realtime STT service."""

    def __init__(
        self,
        settings: Settings,
        on_transcript: TranscriptHandler,
        websocket_connect: Callable[..., Awaitable[ClientConnection]] = connect,
    ) -> None:
        if not settings.sarvam_api_key:
            raise ValueError("Sarvam API key is not configured")

        self._api_key = settings.sarvam_api_key
        self._on_transcript = on_transcript
        self._url = f"{SARVAM_REALTIME_STT_URL}?{urlencode(self._connection_params(settings))}"
        self._websocket_connect = websocket_connect
        self._connection: ClientConnection | None = None
        self._receiver_task: asyncio.Task[None] | None = None

    @staticmethod
    def _connection_params(settings: Settings) -> dict[str, str | int]:
        return {
            "language_code": settings.sarvam_stt_language_code,
            "model": settings.sarvam_stt_model,
            "stream_type": settings.sarvam_stt_stream_type,
            "mode": settings.sarvam_stt_mode,
            "encoding": "linear16",
            "sample_rate": settings.sarvam_stt_sample_rate,
            "endpointing": "vad",
        }

    async def start(self) -> None:
        self._connection = await self._websocket_connect(
            self._url,
            additional_headers={"api-subscription-key": self._api_key},
        )
        self._receiver_task = asyncio.create_task(self._receive_events())

    async def send_audio(self, audio_payload: str) -> None:
        if not self._connection:
            raise RuntimeError("Sarvam realtime STT session has not started")
        await self._connection.send(json.dumps({"event": "audio_input", "audio": audio_payload}))

    async def stop(self) -> None:
        if not self._connection:
            return
        try:
            await self._connection.send(json.dumps({"event": "end"}))
            if self._receiver_task:
                await asyncio.wait_for(asyncio.shield(self._receiver_task), timeout=1)
        except (ConnectionClosed, TimeoutError):
            pass
        finally:
            if self._receiver_task and not self._receiver_task.done():
                self._receiver_task.cancel()
                await asyncio.gather(self._receiver_task, return_exceptions=True)
            await self._connection.close()
            self._connection = None

    async def _receive_events(self) -> None:
        if not self._connection:
            return
        try:
            async for raw_event in self._connection:
                await self._handle_event(json.loads(raw_event))
        except ConnectionClosed:
            return
        except json.JSONDecodeError:
            logger.warning("sarvam_stt_invalid_event")

    async def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("event")
        if event_type not in {"transcript.partial", "transcript.final"}:
            if event_type == "error":
                logger.error("sarvam_stt_error code=%s", event.get("code"))
            return
        transcript = event.get("text")
        if not isinstance(transcript, str) or not transcript.strip():
            return
        language = event.get("language")
        detected_language = language if isinstance(language, str) else None
        await self._on_transcript(event_type, transcript, detected_language)
