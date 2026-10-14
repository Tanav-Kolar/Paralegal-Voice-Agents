from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CallStatus(StrEnum):
    STARTED = "started"
    CONSENT_DECLINED = "consent_declined"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class LanguageChoice(StrEnum):
    ENGLISH = "english"
    HINDI = "hindi"
    HINGLISH = "hinglish"


class TelephonyDetails(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    provider_call_id: str = Field(min_length=1, max_length=255)
    incoming_number: str = Field(min_length=1, max_length=32)
    caller_phone_encrypted: str = Field(min_length=1)
    caller_phone_masked: str = Field(min_length=1, max_length=32)
    started_at: datetime


class DisclosureDetails(BaseModel):
    ai_identity_given_at: datetime | None = None
    recording_disclosure_given_at: datetime | None = None
    recording_consent: bool | None = None
    recording_consent_at: datetime | None = None
    no_advice_disclosure_given_at: datetime | None = None
    no_engagement_disclosure_given_at: datetime | None = None


class LanguageDetails(BaseModel):
    preferred: LanguageChoice | None = None
    detected: list[str] = Field(default_factory=list)
    code_mixed: bool = False


class IntakeCallCreate(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: CallStatus = CallStatus.STARTED
    telephony: TelephonyDetails
    disclosures: DisclosureDetails = Field(default_factory=DisclosureDetails)
    language: LanguageDetails = Field(default_factory=LanguageDetails)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class IntakeCallResponse(IntakeCallCreate):
    pass
