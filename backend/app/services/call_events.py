import logging
from datetime import UTC, datetime
from hashlib import sha256
from json import dumps

logger = logging.getLogger(__name__)


def event_key_for(call_sid: str, event_type: str, fields: dict[str, object]) -> str:
    payload = {
        "provider": "exotel",
        "provider_call_id": call_sid,
        "event_type": event_type,
        "fields": fields,
    }
    return sha256(dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def record_event(collection, call_sid: str, event_type: str, **fields) -> bool:
    event_key = event_key_for(call_sid, event_type, fields)
    result = await collection.update_one(
        {"event_key": event_key},
        {
            "$setOnInsert": {
                "event_key": event_key,
                "provider": "exotel",
                "provider_call_id": call_sid,
                "event_type": event_type,
                "received_at": datetime.now(UTC),
                **fields,
            }
        },
        upsert=True,
    )
    inserted = result.upserted_id is not None
    logger.info(
        "exotel_%s_%s call_sid=%s",
        event_type,
        "received" if inserted else "duplicate",
        call_sid,
    )
    return inserted
