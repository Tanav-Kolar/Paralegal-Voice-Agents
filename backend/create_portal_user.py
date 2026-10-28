"""Provision or update a named portal user: python create_portal_user.py email role."""

import asyncio
import getpass
import sys

from pymongo import AsyncMongoClient

from app.core.config import Settings
from app.security.portal import ROLES, hash_password


async def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in ROLES:
        raise SystemExit("Usage: python create_portal_user.py EMAIL {admin|lawyer|reviewer}")
    email = sys.argv[1].strip().lower()
    if "@" not in email:
        raise SystemExit("A valid email is required")
    name = input("Display name: ").strip()
    if not name:
        raise SystemExit("Display name is required")
    password = getpass.getpass("Password (12+ characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    password_hash = hash_password(password)
    settings = Settings()
    client = AsyncMongoClient(settings.mongodb_uri)
    try:
        users = client[settings.mongodb_database]["portal_users"]
        await users.create_index("email", unique=True)
        await users.update_one(
            {"email": email},
            {
                "$set": {
                    "email": email,
                    "name": name,
                    "role": sys.argv[2],
                    "password_hash": password_hash,
                    "active": True,
                }
            },
            upsert=True,
        )
        print(f"Saved {email} as {sys.argv[2]}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
