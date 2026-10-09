from datetime import datetime
from sqlalchemy import String, Integer, ForeignKey, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.models import Base, uid, now

class StorageObject(Base):
    __tablename__ = "storage_objects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    storage_key: Mapped[str]
    original_filename: Mapped[str | None]
    mime_type: Mapped[str] = mapped_column(String(100))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Reference(Base):
    __tablename__ = "references"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None]
    source_type: Mapped[str] = mapped_column(String(30))
    source_url: Mapped[str | None]
    storage_object_id: Mapped[str | None] = mapped_column(ForeignKey("storage_objects.id"), nullable=True)
    usage_rights_note: Mapped[str | None]
    tags: Mapped[list] = mapped_column(JSON, default=list)
    added_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ReferenceAttribute(Base):
    __tablename__ = "reference_attributes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    reference_id: Mapped[str] = mapped_column(ForeignKey("references.id"), index=True)
    attribute_type: Mapped[str] = mapped_column(String(30))
    value: Mapped[dict] = mapped_column(JSON)
    origin: Mapped[str] = mapped_column(String(30))
    review_status: Mapped[str] = mapped_column(String(30))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ReferenceLink(Base):
    __tablename__ = "reference_links"
    __table_args__ = (UniqueConstraint("reference_id", "target_type", "target_id", "relationship_type"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    reference_id: Mapped[str] = mapped_column(ForeignKey("references.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(30))
    target_id: Mapped[str] = mapped_column(String(36), index=True)
    relationship_type: Mapped[str] = mapped_column(String(80), default="")
    usage_note: Mapped[str | None]
