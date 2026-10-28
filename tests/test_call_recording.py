from io import BytesIO
from wave import open as wave_open

from app.services.call_recording import ConsentGatedCallRecorder


def test_call_recorder_outputs_one_mixed_wav_track() -> None:
    now = [0.0]
    recorder = ConsentGatedCallRecorder(sample_rate=8000, clock=lambda: now[0])
    recorder.append_caller_audio(b"\x01\x00")
    now[0] = 0.000125
    recorder.append_agent_audio(b"\x02\x00")

    track = recorder.to_wav_track()

    assert track and track.filename == "recording-call.wav"
    with wave_open(BytesIO(track.content), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 8000
        assert wav_file.readframes(wav_file.getnframes()) == b"\x01\x00\x02\x00"



def test_call_recorder_mixes_overlapping_audio_and_requires_matching_rates() -> None:
    now = [0.0]
    recorder = ConsentGatedCallRecorder(sample_rate=8000, clock=lambda: now[0])
    recorder.append_caller_audio(b"\x10\x00")
    recorder.append_agent_audio(b"\x20\x00")

    track = recorder.to_wav_track()
    assert track
    with wave_open(BytesIO(track.content), "rb") as wav_file:
        assert wav_file.readframes(wav_file.getnframes()) == b"\x30\x00"

    try:
        ConsentGatedCallRecorder(sample_rate=8000, agent_sample_rate=24000)
    except ValueError as error:
        assert "must match" in str(error)
    else:
        raise AssertionError("Expected mismatched sample rates to be rejected")


def test_fast_chunks_on_same_track_keep_every_sample() -> None:
    now = [0.0]
    recorder = ConsentGatedCallRecorder(sample_rate=8000, clock=lambda: now[0])
    recorder.append_agent_audio(b"\x01\x00\x02\x00")
    recorder.append_agent_audio(b"\x03\x00\x04\x00")

    track = recorder.to_wav_track()
    assert track
    with wave_open(BytesIO(track.content), "rb") as wav_file:
        assert wav_file.readframes(wav_file.getnframes()) == (
            b"\x01\x00\x02\x00\x03\x00\x04\x00"
        )
