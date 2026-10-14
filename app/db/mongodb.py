from collections.abc import AsyncIterator

from fastapi import Request
from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from app.core.config import Settings


class MongoDB:
    """Owns the application's MongoDB client lifecycle."""

    def __init__(self, settings: Settings) -> None:
        self.client = AsyncMongoClient(settings.mongodb_uri)
        self.database = self.client[settings.mongodb_database]

    async def close(self) -> None:
        await self.client.close()


async def get_mongodb(request: Request) -> AsyncIterator[MongoDB]:
    yield request.app.state.mongodb


def get_intake_calls_collection(request: Request) -> AsyncCollection:
    return request.app.state.mongodb.database["intake_calls"]
