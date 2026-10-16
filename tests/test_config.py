from app.core.config import Settings


def test_operational_settings_parse_comma_separated_values() -> None:
    settings = Settings(
        firm_supported_jurisdictions="Delhi, Maharashtra",
    )

    assert settings.firm_supported_jurisdictions == ["Delhi", "Maharashtra"]


def test_provider_settings_use_expected_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.telephony_provider == "unconfigured"
    assert settings.exotel_account_sid == ""
    assert settings.exotel_api_key == ""
    assert settings.exotel_api_token == ""
    assert settings.exotel_api_base_url == "https://api.in.exotel.com"
    assert settings.exotel_webhook_token == ""
    assert settings.public_base_url == ""
    assert settings.exotel_voicebot_username == ""
    assert settings.exotel_voicebot_password == ""
    assert settings.speech_to_text_provider == "sarvam"
    assert settings.text_to_speech_provider == "sarvam"
    assert settings.object_storage_provider == "unconfigured"
    assert settings.local_review_ui_enabled is False
    assert settings.field_encryption_key == ""
