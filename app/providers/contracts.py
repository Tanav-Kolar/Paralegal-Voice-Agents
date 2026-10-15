"""Interfaces that isolate workflow logic from external service vendors."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field


class IncomingCall(BaseModel):
    provider_call_id: str = Field(min_length=1)
    incoming_number: str = Field(min_length=1)
    started_at: datetime


class RecordingReference(BaseModel):
    provider_recording_id: str = Field(min_length=1)
    storage_key: str = Field(min_length=1)
    content_type: str = Field(min_length=1)


class TranscriptSegment(BaseModel):
    speaker: str = Field(min_length=1)
    text: str = Field(min_length=1)
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    detected_language: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class SpeechAudio(BaseModel):
    content: bytes
    content_type: str = Field(min_length=1)


class StoredObject(BaseModel):
    key: str = Field(min_length=1)
    content_type: str = Field(min_length=1)


class TelephonyProvider(Protocol):
    async def start_recording(self, call: IncomingCall) -> None: ...

    async def stop_recording(self, provider_call_id: str) -> RecordingReference | None: ...

    async def end_call(self, provider_call_id: str) -> None: ...


class SpeechToTextProvider(Protocol):
    async def transcribe(self, audio: SpeechAudio) -> list[TranscriptSegment]: ...


TranscriptHandler = Callable[[str, str, str | None], Awaitable[None]]


class StreamingSpeechToTextSession(Protocol):
    async def start(self) -> None: ...

    async def send_audio(self, audio_payload: str) -> None: ...

    async def stop(self) -> None: ...


class TextToSpeechProvider(Protocol):
    async def synthesize(self, text: str, language: str) -> SpeechAudio: ...


class ObjectStorageProvider(Protocol):
    async def store(
        self,
        intake_call_id: UUID,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject: ...

    async def create_download_url(self, key: str) -> str: ...
