from app.core.config import Settings


def test_operational_settings_parse_comma_separated_values() -> None:
    settings = Settings(
        firm_supported_jurisdictions="Delhi, Maharashtra",
    )

    assert settings.firm_supported_jurisdictions == ["Delhi", "Maharashtra"]


def test_provider_settings_use_expected_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.exotel_webhook_token == ""
    assert settings.public_base_url == ""
    assert settings.exotel_voicebot_username == ""
    assert settings.exotel_voicebot_password == ""
    assert settings.speech_to_text_provider == "sarvam"
    assert settings.text_to_speech_provider == "sarvam"
    assert settings.object_storage_provider == "unconfigured"
    assert settings.local_review_api_enabled is False
    assert settings.demo_barge_in_enabled is False
    assert settings.voice_barge_in_enabled is True
    assert settings.sarvam_stt_silence_duration_ms == 1800
    assert settings.sarvam_tts_speaker == "simran_en_customer"
    assert settings.field_encryption_key == ""
