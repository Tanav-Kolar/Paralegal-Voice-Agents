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



def test_call_recorder_writes_each_track_at_its_own_sample_rate() -> None:
    recorder = ConsentGatedCallRecorder(sample_rate=8000, agent_sample_rate=24000)
    recorder.append_caller_audio(b"\x00\x00")
    recorder.append_agent_audio(b"\x00\x00")

    rates = {}
    for track in recorder.to_wav_tracks():
        with wave_open(BytesIO(track.content), "rb") as wav_file:
            rates[track.filename] = wav_file.getframerate()

    assert rates == {"recording-caller.wav": 8000, "recording-agent.wav": 24000}
