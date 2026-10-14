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
    speech_to_text_provider: str = "sarvam"
    text_to_speech_provider: str = "sarvam"
    sarvam_api_key: str = ""
    object_storage_provider: str = "unconfigured"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("firm_supported_jurisdictions", mode="before")
    @classmethod
    def parse_comma_separated_values(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, list):
            return value
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
