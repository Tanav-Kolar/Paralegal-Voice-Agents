"""Append-only audit records for security-relevant intake actions."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class IntakeAuditEventType(StrEnum):
    INTAKE_CREATED = "intake_created"
    CONSENT_RECORDED = "consent_recorded"
    ARTIFACT_ATTACHED = "artifact_attached"
    REVIEW_STATUS_CHANGED = "review_status_changed"
    REVIEW_NOTES_SAVED = "review_notes_saved"
    REPORT_GENERATED = "report_generated"


class IntakeAuditEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    intake_call_id: UUID
    event_type: IntakeAuditEventType
    actor_type: str = Field(default="system", min_length=1, max_length=80)
    details: dict[str, str | bool | int] = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
