"""Encrypted API testing notification channel settings."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiNotificationChannel(PrimaryRecord, Base):
    __tablename__ = "api_notification_channels"
    __table_args__ = (
        UniqueConstraint("owner_id", "project_id", "channel_type"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
