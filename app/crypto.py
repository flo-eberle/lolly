"""
Symmetric encryption for storing FinTS PINs at rest.

The encryption key is derived from the SECRET_KEY environment variable.
Generate a key with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and set it as SECRET_KEY in your .env / docker-compose environment.
"""

import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet

    secret = os.environ.get("SECRET_KEY", "change-me-in-production-please")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"lolly-static-salt",  # fixed salt is fine for local self-hosted use
        iterations=100_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    _fernet = Fernet(key)
    return _fernet


def encrypt_pin(pin: str) -> str:
    return _get_fernet().encrypt(pin.encode()).decode()


def decrypt_pin(encrypted: str) -> str:
    return _get_fernet().decrypt(encrypted.encode()).decode()
