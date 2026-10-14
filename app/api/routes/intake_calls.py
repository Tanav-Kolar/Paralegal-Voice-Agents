from fastapi import APIRouter, Depends, status

from app.db.mongodb import get_intake_calls_collection
from app.schemas.intake_call import IntakeCallCreate, IntakeCallResponse

router = APIRouter()


@router.post("", response_model=IntakeCallResponse, status_code=status.HTTP_201_CREATED)
async def create_intake_call(
    intake_call: IntakeCallCreate,
    collection=Depends(get_intake_calls_collection),
) -> IntakeCallResponse:
    """Create the initial record for a consented or in-progress intake call."""
    document = intake_call.model_dump(mode="json")
    await collection.insert_one(document)
    return IntakeCallResponse(**document)
