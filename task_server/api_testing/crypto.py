"""Authenticated encryption boundary for API testing secrets."""

import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet

from .config import _secret_is_strong


def _non_empty_string(value, name):
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _master_secret():
    secret = _non_empty_string(
        os.getenv("API_TESTING_SECRET_KEY", ""),
        "API_TESTING_SECRET_KEY",
    )
    if not _secret_is_strong(secret):
        raise ValueError("API_TESTING_SECRET_KEY must be a strong random value")
    return secret.encode("utf-8")


def _fernet():
    derived = hashlib.sha256(_master_secret()).digest()
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plaintext: str) -> str:
    value = _non_empty_string(plaintext, "plaintext")
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    value = _non_empty_string(ciphertext, "ciphertext")
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")


def secret_fingerprint(plaintext: str) -> str:
    value = _non_empty_string(plaintext, "plaintext")
    return hmac.new(
        _master_secret(),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12]
