"""Editable environment revision and encrypted secret models."""

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiEnvironment(PrimaryRecord, Base):
    __tablename__ = "api_environments"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_sources.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    active_revision_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(
            "api_environment_revisions.id",
            name="fk_api_env_active_revision_api_env_revisions",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )


class ApiEnvironmentRevision(PrimaryRecord, Base):
    __tablename__ = "api_environment_revisions"
    __table_args__ = (
        UniqueConstraint("environment_id", "revision_number"),
        Index("ix_api_environment_revisions_environment_number", "environment_id", "revision_number"),
    )

    environment_id: Mapped[str] = mapped_column(ForeignKey("api_environments.id", ondelete="CASCADE"), nullable=False)
    source_revision_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_source_revisions.id", ondelete="SET NULL"), nullable=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    default_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiSecretValue(PrimaryRecord, Base):
    __tablename__ = "api_secret_values"
    __table_args__ = (Index("ix_api_secret_values_environment_name", "environment_id", "name"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[str] = mapped_column(ForeignKey("api_environments.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(12), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class ApiEnvironmentVariable(PrimaryRecord, Base):
    __tablename__ = "api_environment_variables"
    __table_args__ = (UniqueConstraint("revision_id", "name"),)

    revision_id: Mapped[str] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="CASCADE"), nullable=False)
    secret_value_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_secret_values.id", ondelete="RESTRICT"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[object] = mapped_column(JSONB, nullable=True)
    is_secret: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    scope: Mapped[str] = mapped_column(String(32), nullable=False, server_default="environment")


class ApiEnvironmentService(PrimaryRecord, Base):
    __tablename__ = "api_environment_services"
    __table_args__ = (UniqueConstraint("revision_id", "service_name"),)

    revision_id: Mapped[str] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="CASCADE"), nullable=False)
    service_name: Mapped[str] = mapped_column(String(200), nullable=False)
    module_name: Mapped[str] = mapped_column(String(200), nullable=False, server_default="default")
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")
