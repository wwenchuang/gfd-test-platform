"""Encrypted third-party provider credentials."""

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiProviderCredential(PrimaryRecord, Base):
    __tablename__ = "api_provider_credentials"
    __table_args__ = (UniqueConstraint("owner_id", "provider"),)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    key_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
