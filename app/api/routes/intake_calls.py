from fastapi import APIRouter, Depends, status

from app.db.mongodb import get_intake_audit_events_collection, get_intake_calls_collection
from app.schemas.intake_call import IntakeCallCreateRequest, IntakeCallResponse
from app.security.field_encryption import FieldEncryptor, get_field_encryptor
from app.services.audit import record_intake_creation_audit_events
from app.services.intake_encryption import encrypt_intake_call

router = APIRouter()


@router.post("", response_model=IntakeCallResponse, status_code=status.HTTP_201_CREATED)
async def create_intake_call(
    intake_call: IntakeCallCreateRequest,
    encryptor: FieldEncryptor = Depends(get_field_encryptor),
    collection=Depends(get_intake_calls_collection),
    audit_collection=Depends(get_intake_audit_events_collection),
) -> IntakeCallResponse:
    """Create an intake record after encrypting all sensitive plaintext fields."""
    encrypted_intake_call = encrypt_intake_call(intake_call, encryptor)
    document = encrypted_intake_call.model_dump(mode="json")
    await collection.insert_one(document)
    await record_intake_creation_audit_events(audit_collection, encrypted_intake_call)
    return IntakeCallResponse(**document)
