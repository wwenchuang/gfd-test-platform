import re

import pytest
from cryptography.fernet import InvalidToken

from task_server.api_testing.crypto import (
    decrypt_secret,
    encrypt_secret,
    secret_fingerprint,
)


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv(
        "API_TESTING_SECRET_KEY",
        "task2-test-only-secret-key-9pR7xQ4mL2vN8cK5",
    )


def test_secret_round_trip_does_not_embed_plaintext():
    plaintext = "business-token"

    encrypted = encrypt_secret(plaintext)

    assert plaintext not in encrypted
    assert decrypt_secret(encrypted) == plaintext


def test_secret_encryption_uses_authenticated_randomized_tokens():
    first = encrypt_secret("business-token")
    second = encrypt_secret("business-token")

    assert first != second
    with pytest.raises(InvalidToken):
        decrypt_secret(first[:-1] + ("A" if first[-1] != "A" else "B"))


@pytest.mark.parametrize("value", [None, "", "   ", b"token", 123])
@pytest.mark.parametrize("operation", [encrypt_secret, decrypt_secret, secret_fingerprint])
def test_secret_operations_accept_only_non_empty_strings(operation, value):
    with pytest.raises((TypeError, ValueError)):
        operation(value)


def test_fingerprint_is_deterministic_hmac_prefix_without_token_material():
    plaintext = "ZXB-sensitive-token-value"

    first = secret_fingerprint(plaintext)
    second = secret_fingerprint(plaintext)

    assert first == second
    assert re.fullmatch(r"[0-9a-f]{12}", first)
    assert first not in plaintext
    assert not any(part and part in first for part in (plaintext[:6], plaintext[-6:]))


def test_changing_master_secret_invalidates_existing_ciphertext(monkeypatch):
    encrypted = encrypt_secret("business-token")
    monkeypatch.setenv(
        "API_TESTING_SECRET_KEY",
        "different-test-only-secret-key-4mK8vQ2rP9xN6cL3",
    )

    with pytest.raises(InvalidToken):
        decrypt_secret(encrypted)
