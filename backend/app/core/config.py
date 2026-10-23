from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    app_name: str = "Paralegal Voice Agents API"
    app_env: str = "development"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "paralegal_voice_agents"
    firm_name: str = "Victus Family Law Firm"
    firm_primary_phone: str = ""
    firm_office_timezone: str = "Asia/Kolkata"
    firm_office_hours: str = "Monday-Friday 09:30-18:30"
    firm_supported_jurisdictions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["India"]
    )
    after_hours_agent_enabled: bool = True
    callback_target_business_hours: int = Field(default=1, ge=1, le=120)
    telephony_provider: str = "unconfigured"
    exotel_account_sid: str = ""
    exotel_api_key: str = ""
    exotel_api_token: str = ""
    exotel_api_base_url: str = "https://api.in.exotel.com"
    exotel_webhook_token: str = ""
    public_base_url: str = ""
    exotel_voicebot_username: str = ""
    exotel_voicebot_password: str = ""
    speech_to_text_provider: str = "sarvam"
    text_to_speech_provider: str = "sarvam"
    sarvam_api_key: str = ""
    sarvam_chat_model: str = "sarvam-105b-conversations"
    sarvam_chat_timeout_seconds: float = Field(default=15, gt=0, le=60)
    sarvam_stt_language_code: str = "auto"
    sarvam_stt_model: str = "saaras:v3-realtime"
    sarvam_stt_stream_type: str = "fast"
    sarvam_stt_sample_rate: int = 8000
    sarvam_stt_mode: str = "codemix"
    sarvam_tts_model: str = "bulbul:v4-flash"
    sarvam_tts_language_code: str = "en-IN"
    sarvam_tts_speaker: str = "simran_en_customer"
    sarvam_tts_hindi_speaker: str = "aparna_hi_customer"
    sarvam_tts_sample_rate: int = 8000
    object_storage_provider: str = "unconfigured"
    gcs_project_id: str = ""
    gcs_bucket_name: str = ""
    gcs_service_account_key_path: str = ""
    gcs_signed_url_ttl_minutes: int = Field(default=10, ge=1, le=60)
    local_review_api_enabled: bool = False
    demo_mode_enabled: bool = False
    demo_access_code: str = ""
    access_cookie_secret: str = ""
    demo_session_max_seconds: int = Field(default=300, ge=30, le=1800)
    demo_max_concurrent_sessions: int = Field(default=3, ge=1, le=20)
    field_encryption_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("firm_supported_jurisdictions", mode="before")
    @classmethod
    def parse_comma_separated_values(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        return [item.strip() for item in value.split(",") if item.strip()]

    @field_validator("sarvam_stt_sample_rate")
    @classmethod
    def validate_sarvam_stt_sample_rate(cls, value: int) -> int:
        if value not in {8000, 16000}:
            raise ValueError("SARVAM_STT_SAMPLE_RATE must be 8000 or 16000")
        return value

    @field_validator("sarvam_tts_sample_rate")
    @classmethod
    def validate_sarvam_tts_sample_rate(cls, value: int) -> int:
        if value not in {8000, 16000, 22050, 24000}:
            raise ValueError("SARVAM_TTS_SAMPLE_RATE must be a supported streaming sample rate")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
