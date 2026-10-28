from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class CallStatus(StrEnum):
    STARTED = "started"
    CONSENT_PENDING = "consent_pending"
    CONSENT_DECLINED = "consent_declined"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class LanguageChoice(StrEnum):
    ENGLISH = "english"
    HINDI = "hindi"
    HINGLISH = "hinglish"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    REVIEWED = "reviewed"
    CALLBACK_ATTEMPTED = "callback_attempted"
    ENGAGEMENT_DECLINED = "engagement_declined"
    ENGAGED_OFF_PLATFORM = "engaged_off_platform"


class PartyRole(StrEnum):
    CALLER = "caller"
    OTHER_PARTY = "other_party"
    CHILD = "child"
    DECEASED_PERSON = "deceased_person"
    OTHER = "other"


class TranscriptSpeaker(StrEnum):
    AGENT = "agent"
    CALLER = "caller"
    SYSTEM = "system"


class ArtifactKind(StrEnum):
    CALL_RECORDING = "call_recording"
    TRANSCRIPT_EXPORT = "transcript_export"
    INTAKE_REPORT = "intake_report"


class TelephonyDetails(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    provider_call_id: str = Field(min_length=1, max_length=255)
    caller_phone_encrypted: str = Field(min_length=1)
    caller_phone_masked: str = Field(min_length=1, max_length=32)
    started_at: datetime
    stream_sid: str | None = Field(default=None, max_length=255)


class TelephonyDetailsInput(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    provider_call_id: str = Field(min_length=1, max_length=255)
    caller_phone: str = Field(min_length=1, max_length=32)
    started_at: datetime


class DisclosureDetails(BaseModel):
    ai_identity_given_at: datetime | None = None
    recording_disclosure_given_at: datetime | None = None
    recording_consent: bool | None = None
    recording_consent_at: datetime | None = None
    recording_started_at: datetime | None = None
    recording_stopped_at: datetime | None = None
    intake_consent: bool | None = None
    intake_consent_at: datetime | None = None
    no_advice_disclosure_given_at: datetime | None = None
    no_engagement_disclosure_given_at: datetime | None = None


class LanguageDetails(BaseModel):
    preferred: LanguageChoice | None = None
    detected: list[str] = Field(default_factory=list)
    code_mixed: bool = False


class Party(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    role: PartyRole
    name_encrypted: str | None = None
    name_masked: str | None = Field(default=None, max_length=160)
    relationship_to_caller_encrypted: str | None = None


class PartyInput(BaseModel):
    role: PartyRole
    name: str | None = Field(default=None, max_length=160)
    relationship_to_caller: str | None = Field(default=None, max_length=160)


class CaseFact(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    field_name: str = Field(min_length=1, max_length=160)
    value_encrypted: str | None = None
    source_transcript_segment_ids: list[UUID] = Field(default_factory=list)
    provided_by_caller: bool = True
    not_provided: bool = False


class CaseFactInput(BaseModel):
    field_name: str = Field(min_length=1, max_length=160)
    value: str | None = None
    source_transcript_segment_ids: list[UUID] = Field(default_factory=list)
    provided_by_caller: bool = True
    not_provided: bool = False

    @model_validator(mode="after")
    def validate_value_or_absence(self) -> "CaseFactInput":
        if self.not_provided and self.value:
            raise ValueError("A fact cannot contain a value and be marked not provided")
        if not self.not_provided and not self.value:
            raise ValueError("A fact value is required unless marked not provided")
        return self


class TranscriptSegment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    speaker: TranscriptSpeaker
    text_encrypted: str = Field(min_length=1)
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    detected_language: str | None = Field(default=None, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)


class TranscriptSegmentInput(BaseModel):
    speaker: TranscriptSpeaker
    text: str = Field(min_length=1)
    started_at_ms: int = Field(ge=0)
    ended_at_ms: int = Field(ge=0)
    detected_language: str | None = Field(default=None, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ProtectedObjectReference(BaseModel):
    kind: ArtifactKind
    storage_provider: str = Field(min_length=1, max_length=80)
    storage_key: str = Field(min_length=1, max_length=1024)
    content_type: str = Field(min_length=1, max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("storage_key")
    @classmethod
    def validate_storage_key(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("Storage key must be a relative object key")
        return value


class GeneratedReport(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    version: int = Field(ge=1)
    artifact: ProtectedObjectReference
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ai_generated: bool = True
    unverified_facts_disclaimer_included: bool = True


class LawyerReview(BaseModel):
    status: ReviewStatus = ReviewStatus.PENDING
    reviewed_at: datetime | None = None
    internal_notes_encrypted: str | None = None


class LawyerReviewInput(BaseModel):
    status: ReviewStatus = ReviewStatus.PENDING
    reviewed_at: datetime | None = None
    internal_notes: str | None = None


class IntakeDetails(BaseModel):
    matter_type: str | None = Field(default=None, max_length=80)
    parties: list[Party] = Field(default_factory=list)
    facts: list[CaseFact] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    artifacts: list[ProtectedObjectReference] = Field(default_factory=list)
    reports: list[GeneratedReport] = Field(default_factory=list)


class IntakeDetailsInput(BaseModel):
    matter_type: str | None = Field(default=None, max_length=80)
    parties: list[PartyInput] = Field(default_factory=list)
    facts: list[CaseFactInput] = Field(default_factory=list)
    transcript_segments: list[TranscriptSegmentInput] = Field(default_factory=list)
    artifacts: list[ProtectedObjectReference] = Field(default_factory=list)
    reports: list[GeneratedReport] = Field(default_factory=list)


class IntakeCallCreate(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: CallStatus = CallStatus.STARTED
    telephony: TelephonyDetails
    disclosures: DisclosureDetails = Field(default_factory=DisclosureDetails)
    language: LanguageDetails = Field(default_factory=LanguageDetails)
    intake: IntakeDetails = Field(default_factory=IntakeDetails)
    lawyer_review: LawyerReview = Field(default_factory=LawyerReview)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IntakeCallCreateRequest(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    status: CallStatus = CallStatus.STARTED
    telephony: TelephonyDetailsInput
    disclosures: DisclosureDetails = Field(default_factory=DisclosureDetails)
    language: LanguageDetails = Field(default_factory=LanguageDetails)
    intake: IntakeDetailsInput = Field(default_factory=IntakeDetailsInput)
    lawyer_review: LawyerReviewInput = Field(default_factory=LawyerReviewInput)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IntakeCallResponse(IntakeCallCreate):
    pass
