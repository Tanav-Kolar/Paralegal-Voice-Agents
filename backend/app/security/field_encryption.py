"""Field-level authenticated encryption for sensitive intake data."""

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, HTTPException, status

from app.core.config import Settings, get_settings

_ENCRYPTION_PREFIX = "fernet:v1:"


class FieldEncryptor:
    """Encrypt and decrypt UTF-8 field values with a versioned Fernet token."""

    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode())
        except (TypeError, ValueError) as error:
            raise ValueError("Field encryption key is invalid") from error

    def encrypt(self, plaintext: str) -> str:
        return f"{_ENCRYPTION_PREFIX}{self._fernet.encrypt(plaintext.encode()).decode()}"

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(_ENCRYPTION_PREFIX):
            raise ValueError("Encrypted field has an unsupported format")
        try:
            token = ciphertext.removeprefix(_ENCRYPTION_PREFIX).encode()
            return self._fernet.decrypt(token).decode()
        except (InvalidToken, UnicodeDecodeError) as error:
            raise ValueError("Encrypted field cannot be decrypted") from error


def get_field_encryptor(settings: Settings = Depends(get_settings)) -> FieldEncryptor:
    if not settings.field_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Field encryption key is not configured",
        )
    try:
        return FieldEncryptor(settings.field_encryption_key)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Field encryption key is invalid",
        ) from error


def get_optional_field_encryptor(
    settings: Settings = Depends(get_settings),
) -> FieldEncryptor | None:
    """Allow metadata review when a legacy deployment key is unavailable."""
    if not settings.field_encryption_key:
        return None
    try:
        return FieldEncryptor(settings.field_encryption_key)
    except ValueError:
        return None
