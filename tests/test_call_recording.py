from io import BytesIO
from wave import open as wave_open

from app.services.call_recording import ConsentGatedCallRecorder


def test_call_recorder_outputs_only_non_empty_wav_tracks() -> None:
    recorder = ConsentGatedCallRecorder(sample_rate=8000)
    recorder.append_caller_audio(b"caller-pcm")

    tracks = recorder.to_wav_tracks()

    assert [track.filename for track in tracks] == ["recording-caller.wav"]
    with wave_open(BytesIO(tracks[0].content), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 8000
        assert wav_file.readframes(wav_file.getnframes()) == b"caller-pcm"

