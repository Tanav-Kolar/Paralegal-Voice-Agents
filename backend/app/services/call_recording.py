"""Consent-gated, time-aligned PCM recording for a single voicebot session."""

from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from time import monotonic
from wave import open as wave_open


@dataclass(frozen=True)
class RecordedAudioTrack:
    """A playable WAV track produced after the caller has granted consent."""

    filename: str
    content: bytes


class ConsentGatedCallRecorder:
    """Mix post-consent caller and agent Linear16 audio into one call recording."""

    def __init__(
        self,
        sample_rate: int,
        agent_sample_rate: int | None = None,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if agent_sample_rate not in (None, sample_rate):
            raise ValueError("Caller and agent sample rates must match for a mixed recording")
        self._sample_rate = sample_rate
        self._clock = clock
        self._started_at = clock()
        self._mixed_pcm = bytearray()

    def append_caller_audio(self, pcm_audio: bytes) -> None:
        """Mix a caller Linear16 frame at its place in the call timeline."""
        self._mix_at_current_time(pcm_audio)

    def append_agent_audio(self, pcm_audio: bytes) -> None:
        """Mix an agent Linear16 frame at its place in the call timeline."""
        self._mix_at_current_time(pcm_audio)

    def to_wav_track(self) -> RecordedAudioTrack | None:
        """Return one standard mono WAV recording, or None when no audio was captured."""
        if not self._mixed_pcm:
            return None
        return RecordedAudioTrack(
            filename="recording-call.wav",
            content=self._as_wav(bytes(self._mixed_pcm), self._sample_rate),
        )

    def _mix_at_current_time(self, pcm_audio: bytes) -> None:
        usable = len(pcm_audio) - len(pcm_audio) % 2
        if usable == 0:
            return
        offset_samples = max(0, int((self._clock() - self._started_at) * self._sample_rate))
        offset = offset_samples * 2
        required = offset + usable
        if required > len(self._mixed_pcm):
            self._mixed_pcm.extend(b"\x00" * (required - len(self._mixed_pcm)))
        for index in range(0, usable, 2):
            position = offset + index
            existing = int.from_bytes(
                self._mixed_pcm[position : position + 2], "little", signed=True
            )
            incoming = int.from_bytes(pcm_audio[index : index + 2], "little", signed=True)
            mixed = max(-32768, min(32767, existing + incoming))
            self._mixed_pcm[position : position + 2] = mixed.to_bytes(2, "little", signed=True)

    @staticmethod
    def _as_wav(pcm_audio: bytes, sample_rate: int) -> bytes:
        output = BytesIO()
        with wave_open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_audio)
        return output.getvalue()
