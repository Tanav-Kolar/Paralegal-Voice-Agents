from app.core.config import Settings


def test_operational_settings_parse_comma_separated_values() -> None:
    settings = Settings(
        firm_supported_jurisdictions="Delhi, Maharashtra",
    )

    assert settings.firm_supported_jurisdictions == ["Delhi", "Maharashtra"]


def test_provider_settings_use_expected_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.telephony_provider == "unconfigured"
    assert settings.speech_to_text_provider == "sarvam"
    assert settings.text_to_speech_provider == "sarvam"
    assert settings.object_storage_provider == "unconfigured"
