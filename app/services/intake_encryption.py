"""Convert intake requests into encrypted persistence records."""

from app.schemas.intake_call import (
    CaseFact,
    IntakeCallCreate,
    IntakeCallCreateRequest,
    IntakeDetails,
    LawyerReview,
    Party,
    TelephonyDetails,
    TranscriptSegment,
)
from app.security.field_encryption import FieldEncryptor


def encrypt_intake_call(
    request: IntakeCallCreateRequest, encryptor: FieldEncryptor
) -> IntakeCallCreate:
    """Encrypt plaintext caller-provided values before they reach persistence."""
    return IntakeCallCreate(
        id=request.id,
        status=request.status,
        telephony=TelephonyDetails(
            provider=request.telephony.provider,
            provider_call_id=request.telephony.provider_call_id,
            caller_phone_encrypted=encryptor.encrypt(request.telephony.caller_phone),
            caller_phone_masked=_mask_phone(request.telephony.caller_phone),
            started_at=request.telephony.started_at,
        ),
        disclosures=request.disclosures,
        language=request.language,
        intake=IntakeDetails(
            matter_type=request.intake.matter_type,
            parties=[
                Party(
                    role=party.role,
                    name_encrypted=encryptor.encrypt(party.name) if party.name else None,
                    name_masked=_mask_name(party.name) if party.name else None,
                    relationship_to_caller_encrypted=(
                        encryptor.encrypt(party.relationship_to_caller)
                        if party.relationship_to_caller
                        else None
                    ),
                )
                for party in request.intake.parties
            ],
            facts=[
                CaseFact(
                    field_name=fact.field_name,
                    value_encrypted=encryptor.encrypt(fact.value) if fact.value else None,
                    source_transcript_segment_ids=fact.source_transcript_segment_ids,
                    provided_by_caller=fact.provided_by_caller,
                    not_provided=fact.not_provided,
                )
                for fact in request.intake.facts
            ],
            transcript_segments=[
                TranscriptSegment(
                    speaker=segment.speaker,
                    text_encrypted=encryptor.encrypt(segment.text),
                    started_at_ms=segment.started_at_ms,
                    ended_at_ms=segment.ended_at_ms,
                    detected_language=segment.detected_language,
                    confidence=segment.confidence,
                )
                for segment in request.intake.transcript_segments
            ],
            artifacts=request.intake.artifacts,
            reports=request.intake.reports,
        ),
        lawyer_review=LawyerReview(
            status=request.lawyer_review.status,
            reviewed_at=request.lawyer_review.reviewed_at,
            internal_notes_encrypted=(
                encryptor.encrypt(request.lawyer_review.internal_notes)
                if request.lawyer_review.internal_notes
                else None
            ),
        ),
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def _mask_phone(phone: str) -> str:
    if len(phone) <= 4:
        return "*" * len(phone)
    return f"{'*' * (len(phone) - 4)}{phone[-4:]}"


def _mask_name(name: str) -> str:
    if len(name) == 1:
        return "*"
    return f"{name[0]}{'*' * (len(name) - 1)}"
