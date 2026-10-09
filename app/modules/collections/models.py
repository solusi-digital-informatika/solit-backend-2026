from datetime import datetime
from sqlalchemy import ForeignKey, String, Integer, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.models import Base, uid, now

class Collection(Base):
    __tablename__ = "collections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None]
    collection_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class CollectionRevision(Base):
    __tablename__ = "collection_revisions"
    __table_args__ = (UniqueConstraint("collection_id", "revision_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id"), index=True)
    revision_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    change_summary: Mapped[str | None]
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class CollectionItem(Base):
    __tablename__ = "collection_items"
    __table_args__ = (UniqueConstraint("collection_revision_id", "asset_id"), UniqueConstraint("collection_revision_id", "position"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    collection_revision_id: Mapped[str] = mapped_column(ForeignKey("collection_revisions.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    asset_version_id: Mapped[str] = mapped_column(ForeignKey("asset_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    role: Mapped[str | None]
    note: Mapped[str | None]

class Approval(Base):
    __tablename__ = "collection_approvals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    collection_revision_id: Mapped[str] = mapped_column(ForeignKey("collection_revisions.id"), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str | None]
    reviewed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class PinDecision(Base):
    __tablename__ = "collection_pin_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    collection_revision_id: Mapped[str] = mapped_column(ForeignKey("collection_revisions.id"))
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    asset_version_id: Mapped[str] = mapped_column(ForeignKey("asset_versions.id"))
    decision_type: Mapped[str] = mapped_column(String(40))
    rationale: Mapped[str]
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
