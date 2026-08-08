"""Versioned API source models."""

from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiSource(PrimaryRecord, Base):
    __tablename__ = "api_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "name"),
        ForeignKeyConstraint(
            ["id", "active_revision_id"],
            ["api_source_revisions.source_id", "api_source_revisions.id"],
            name="fk_api_sources_active_revision_parent",
            use_alter=True,
        ),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    active_revision_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    connection_config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiSourceRevision(PrimaryRecord, Base):
    __tablename__ = "api_source_revisions"
    __table_args__ = (
        UniqueConstraint("source_id", "revision_number"),
        UniqueConstraint("source_id", "id"),
        Index("ix_api_source_revisions_source_number", "source_id", "revision_number"),
    )

    source_id: Mapped[str] = mapped_column(ForeignKey("api_sources.id", ondelete="CASCADE"), nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    document_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    import_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    activated_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiSourceEndpoint(PrimaryRecord, Base):
    __tablename__ = "api_source_endpoints"
    __table_args__ = (
        UniqueConstraint("revision_id", "stable_key"),
        Index(
            "ix_api_source_endpoints_revision_method_path",
            "revision_id",
            "method",
            "normalized_path",
        ),
    )

    revision_id: Mapped[str] = mapped_column(ForeignKey("api_source_revisions.id", ondelete="CASCADE"), nullable=False)
    stable_key: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(300), nullable=False, server_default="")
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_path: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    operation: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ApiSourceSchema(PrimaryRecord, Base):
    __tablename__ = "api_source_schemas"
    __table_args__ = (UniqueConstraint("revision_id", "schema_key"),)

    revision_id: Mapped[str] = mapped_column(ForeignKey("api_source_revisions.id", ondelete="CASCADE"), nullable=False)
    schema_key: Mapped[str] = mapped_column(String(300), nullable=False)
    schema: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ApiSourceDiff(PrimaryRecord, Base):
    __tablename__ = "api_source_diffs"
    __table_args__ = (Index("ix_api_source_diffs_source_created", "source_id", "created_at"),)

    source_id: Mapped[str] = mapped_column(ForeignKey("api_sources.id", ondelete="CASCADE"), nullable=False)
    previous_revision_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_source_revisions.id", ondelete="SET NULL"), nullable=True)
    candidate_revision_id: Mapped[str] = mapped_column(ForeignKey("api_source_revisions.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="preview")
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changes: Mapped[list] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
