"""Write append-only, non-sensitive audit events for intake operations."""

from app.schemas.audit_event import IntakeAuditEvent, IntakeAuditEventType
from app.schemas.intake_call import IntakeCallCreate


async def record_audit_event(
    collection,
    *,
    intake_call_id,
    event_type: IntakeAuditEventType,
    details: dict[str, str | bool | int] | None = None,
    actor_type: str = "system",
) -> IntakeAuditEvent:
    """Insert an audit event; audit records are never updated or deleted by this service."""
    event = IntakeAuditEvent(
        intake_call_id=intake_call_id,
        event_type=event_type,
        actor_type=actor_type,
        details=details or {},
    )
    await collection.insert_one(event.model_dump(mode="json"))
    return event


async def record_intake_creation_audit_events(
    collection, intake_call: IntakeCallCreate
) -> list[IntakeAuditEvent]:
    """Record safe creation-time events without copying sensitive intake content."""
    events = [
        await record_audit_event(
            collection,
            intake_call_id=intake_call.id,
            event_type=IntakeAuditEventType.INTAKE_CREATED,
            details={"call_status": intake_call.status.value},
        )
    ]
    if intake_call.disclosures.intake_consent is not None:
        events.append(
            await record_audit_event(
                collection,
                intake_call_id=intake_call.id,
                event_type=IntakeAuditEventType.CONSENT_RECORDED,
                details={"intake_consent": intake_call.disclosures.intake_consent},
            )
        )
    for artifact in intake_call.intake.artifacts:
        events.append(
            await record_audit_event(
                collection,
                intake_call_id=intake_call.id,
                event_type=IntakeAuditEventType.ARTIFACT_ATTACHED,
                details={"artifact_kind": artifact.kind.value},
            )
        )
    return events
