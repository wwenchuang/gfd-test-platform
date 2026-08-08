"""API testing project model."""

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiProject(PrimaryRecord, Base):
    __tablename__ = "api_projects"
    __table_args__ = (UniqueConstraint("slug"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiProjectMember(PrimaryRecord, Base):
    """Reserved membership boundary; authorization behavior is deferred."""

    __tablename__ = "api_project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "identity_type", "member_identity"),
        Index("ix_api_project_members_project_status", "project_id", "status"),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False
    )
    member_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    identity_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="platform_user"
    )
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="viewer"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="active"
    )
