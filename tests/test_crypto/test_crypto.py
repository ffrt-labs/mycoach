"""Tests for credential encryption utilities."""

import pytest
from cryptography.fernet import InvalidToken

from mycoach.crypto import decrypt_credentials, encrypt_credentials, generate_key


def test_generate_key_returns_valid_base64() -> None:
    key = generate_key()
    assert isinstance(key, str)
    assert len(key) == 44  # Fernet keys are 44 chars base64


def test_generate_key_unique() -> None:
    key1 = generate_key()
    key2 = generate_key()
    assert key1 != key2


def test_encrypt_decrypt_roundtrip() -> None:
    key = generate_key()
    creds = {"email": "user@example.com", "password": "s3cr3t"}
    encrypted = encrypt_credentials(creds, key)
    decrypted = decrypt_credentials(encrypted, key)
    assert decrypted == creds


def test_encrypted_is_not_plaintext() -> None:
    key = generate_key()
    creds = {"password": "mysecretpassword"}
    encrypted = encrypt_credentials(creds, key)
    assert "mysecretpassword" not in encrypted


def test_decrypt_with_wrong_key_raises() -> None:
    key1 = generate_key()
    key2 = generate_key()
    creds = {"token": "abc123"}
    encrypted = encrypt_credentials(creds, key1)
    with pytest.raises(InvalidToken):
        decrypt_credentials(encrypted, key2)


def test_decrypt_corrupted_data_raises() -> None:
    key = generate_key()
    with pytest.raises(InvalidToken):
        decrypt_credentials("not-a-valid-token-at-all!!", key)


def test_encrypt_empty_dict() -> None:
    key = generate_key()
    creds: dict[str, str] = {}
    encrypted = encrypt_credentials(creds, key)
    decrypted = decrypt_credentials(encrypted, key)
    assert decrypted == {}


def test_encrypt_nested_values() -> None:
    key = generate_key()
    creds = {"token_data": {"access": "abc", "refresh": "def"}}
    encrypted = encrypt_credentials(creds, key)  # type: ignore[arg-type]
    decrypted = decrypt_credentials(encrypted, key)
    assert decrypted == creds
