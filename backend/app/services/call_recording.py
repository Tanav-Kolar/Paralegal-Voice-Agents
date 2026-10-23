"""Consent-gated in-memory PCM recording for a single voicebot session."""

from dataclasses import dataclass
from io import BytesIO
from wave import open as wave_open


@dataclass(frozen=True)
class RecordedAudioTrack:
    """A playable WAV track produced after the caller has granted consent."""

    filename: str
    content: bytes


class ConsentGatedCallRecorder:
    """Keep post-consent Linear16 audio in separate caller and agent tracks."""

    def __init__(self, sample_rate: int, agent_sample_rate: int | None = None) -> None:
        self._caller_rate = sample_rate
        self._agent_rate = agent_sample_rate or sample_rate
        self._caller_pcm = bytearray()
        self._agent_pcm = bytearray()

    def append_caller_audio(self, pcm_audio: bytes) -> None:
        """Append a caller Linear16 PCM frame."""
        self._caller_pcm.extend(pcm_audio)

    def append_agent_audio(self, pcm_audio: bytes) -> None:
        """Append an agent Linear16 PCM frame."""
        self._agent_pcm.extend(pcm_audio)

    def to_wav_tracks(self) -> list[RecordedAudioTrack]:
        """Return non-empty, standard mono WAV tracks suitable for private storage."""
        tracks: list[RecordedAudioTrack] = []
        for filename, pcm_audio, sample_rate in (
            ("recording-caller.wav", self._caller_pcm, self._caller_rate),
            ("recording-agent.wav", self._agent_pcm, self._agent_rate),
        ):
            if pcm_audio:
                tracks.append(
                    RecordedAudioTrack(
                        filename=filename,
                        content=self._as_wav(bytes(pcm_audio), sample_rate),
                    )
                )
        return tracks

    @staticmethod
    def _as_wav(pcm_audio: bytes, sample_rate: int) -> bytes:
        output = BytesIO()
        with wave_open(output, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_audio)
        return output.getvalue()
