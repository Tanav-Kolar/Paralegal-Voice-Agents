from collections.abc import AsyncIterator

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from starlette.requests import HTTPConnection

from app.core.config import Settings


class MongoDB:
    """Owns the application's MongoDB client lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self.client = AsyncMongoClient(settings.mongodb_uri)
        self.database = self.client[settings.mongodb_database]

    async def close(self) -> None:
        await self.client.close()


async def get_mongodb(connection: HTTPConnection) -> AsyncIterator[MongoDB]:
    yield connection.app.state.mongodb


def get_call_events_collection(connection: HTTPConnection) -> AsyncCollection:
    return connection.app.state.mongodb.database["call_events"]


def get_intake_calls_collection(connection: HTTPConnection) -> AsyncCollection:
    return connection.app.state.mongodb.database["intake_calls"]


def get_intake_audit_events_collection(connection: HTTPConnection) -> AsyncCollection:
    return connection.app.state.mongodb.database["intake_audit_events"]
