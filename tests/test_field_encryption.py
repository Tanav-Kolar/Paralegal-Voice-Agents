import pytest
from cryptography.fernet import Fernet

from app.security.field_encryption import FieldEncryptor


def test_field_encryptor_round_trips_a_value() -> None:
    encryptor = FieldEncryptor(Fernet.generate_key().decode())

    encrypted = encryptor.encrypt("Sensitive caller fact")

    assert encrypted.startswith("fernet:v1:")
    assert "Sensitive caller fact" not in encrypted
    assert encryptor.decrypt(encrypted) == "Sensitive caller fact"


def test_field_encryptor_rejects_unsupported_ciphertext() -> None:
    encryptor = FieldEncryptor(Fernet.generate_key().decode())

    with pytest.raises(ValueError, match="unsupported format"):
        encryptor.decrypt("plaintext")
