"""Encrypted provider credential boundary."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..crypto import decrypt_secret, encrypt_secret, secret_fingerprint
from ..repositories.provider_repository import ProviderRepository


APIFOX_PROVIDER = "apifox"


class ProviderCredentialInputError(ValueError):
    pass


class ProviderCredentialNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ProviderCredentialView:
    provider: str
    configured: bool
    fingerprint: str
    updated_at: Optional[datetime]


def _owner(value):
    if not isinstance(value, str) or not value.strip():
        raise ProviderCredentialInputError("owner id must not be empty")
    return value.strip()


def _token(value):
    if not isinstance(value, str) or not value.strip():
        raise ProviderCredentialInputError("Apifox access token must not be empty")
    return value.strip()


class ProviderService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def save_apifox_credential(self, owner_id, token, actor_id):
        owner = _owner(owner_id)
        actor = _owner(actor_id)
        plaintext = _token(token)
        ciphertext = encrypt_secret(plaintext)
        fingerprint = secret_fingerprint(plaintext)
        with self._session_factory.begin() as session:
            repository = ProviderRepository(session)
            record = repository.get(owner, APIFOX_PROVIDER, for_update=True)
            if record is None:
                record = repository.create(
                    owner,
                    APIFOX_PROVIDER,
                    ciphertext,
                    fingerprint,
                    actor,
                )
            else:
                record.ciphertext = ciphertext
                record.fingerprint = fingerprint
                record.key_version = 1
                record.updated_by = actor
                repository.flush()
            return self._view(record)

    def get_apifox_credential(self, owner_id):
        owner = _owner(owner_id)
        with self._session_factory() as session:
            record = ProviderRepository(session).get(owner, APIFOX_PROVIDER)
            if record is None:
                return ProviderCredentialView(
                    provider=APIFOX_PROVIDER,
                    configured=False,
                    fingerprint="",
                    updated_at=None,
                )
            return self._view(record)

    def require_apifox_token(self, owner_id):
        owner = _owner(owner_id)
        with self._session_factory() as session:
            record = ProviderRepository(session).get(owner, APIFOX_PROVIDER)
            if record is None:
                raise ProviderCredentialNotFoundError(
                    "Apifox access token is not configured"
                )
            return decrypt_secret(record.ciphertext)

    @staticmethod
    def _view(record):
        return ProviderCredentialView(
            provider=record.provider,
            configured=True,
            fingerprint=record.fingerprint,
            updated_at=record.updated_at,
        )
