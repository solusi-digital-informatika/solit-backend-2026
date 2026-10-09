from datetime import datetime
from sqlalchemy import String, Integer, ForeignKey, UniqueConstraint, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.models import Base, now, uid

class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None]
    asset_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class AssetVersion(Base):
    __tablename__ = "asset_versions"
    __table_args__ = (UniqueConstraint("asset_id", "version_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    derived_from_version_id: Mapped[str | None] = mapped_column(ForeignKey("asset_versions.id"), nullable=True)
    direction_revision_id: Mapped[str | None] = mapped_column(ForeignKey("direction_revisions.id"), nullable=True)
    brief_revision_id: Mapped[str | None] = mapped_column(ForeignKey("brief_revisions.id"), nullable=True)
    content: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class VersionDecision(Base):
    __tablename__ = "asset_version_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    asset_version_id: Mapped[str] = mapped_column(ForeignKey("asset_versions.id"), index=True)
    decision_type: Mapped[str] = mapped_column(String(40))
    rationale: Mapped[str]
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
