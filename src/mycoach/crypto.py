"""Credential encryption utilities using Fernet symmetric encryption.

Provides encrypt/decrypt functions for storing sensitive credentials
(API keys, passwords, tokens) in the database at rest.
"""

import json
import logging
from typing import Any

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)


def generate_key() -> str:
    """Generate a new Fernet encryption key. Use once to create MYCOACH_ENCRYPTION_KEY."""
    return Fernet.generate_key().decode()


def _get_fernet(key: str) -> Fernet:
    """Create a Fernet instance from a base64-encoded key string."""
    return Fernet(key.encode())


def encrypt_credentials(credentials: dict[str, Any], key: str) -> str:
    """Encrypt a credentials dict to a string for database storage.

    Args:
        credentials: Dict of credential key-value pairs (e.g. {"email": "...", "password": "..."})
        key: Fernet encryption key (base64-encoded, 32 bytes).

    Returns:
        Encrypted string (base64-encoded Fernet token).
    """
    f = _get_fernet(key)
    plaintext = json.dumps(credentials).encode()
    return f.encrypt(plaintext).decode()


def decrypt_credentials(encrypted: str, key: str) -> dict[str, Any]:
    """Decrypt an encrypted credentials string back to a dict.

    Args:
        encrypted: Encrypted string from encrypt_credentials().
        key: Same Fernet encryption key used for encryption.

    Returns:
        Decrypted credentials dictionary.

    Raises:
        InvalidToken: If the key is wrong or data is corrupted.
    """
    f = _get_fernet(key)
    plaintext = f.decrypt(encrypted.encode())
    result: dict[str, Any] = json.loads(plaintext)
    return result
