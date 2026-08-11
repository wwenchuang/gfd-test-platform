"""Owner-scoped provider credential persistence."""

from sqlalchemy import select

from ..models.provider import ApiProviderCredential
from .source_repository import audit_fields


class ProviderRepository:
    def __init__(self, session):
        self.session = session

    def get(self, owner_id, provider, *, for_update=False):
        query = select(ApiProviderCredential).where(
            ApiProviderCredential.owner_id == owner_id,
            ApiProviderCredential.provider == provider,
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def create(self, owner_id, provider, ciphertext, fingerprint, actor_id):
        record = ApiProviderCredential(
            provider=provider,
            ciphertext=ciphertext,
            fingerprint=fingerprint,
            key_version=1,
            **audit_fields(actor_id),
        )
        record.owner_id = owner_id
        self.session.add(record)
        self.session.flush()
        return record

    def flush(self):
        self.session.flush()
