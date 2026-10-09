from datetime import datetime
from sqlalchemy import String, ForeignKey, Integer, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.models import Base, uid, now

class Assessment(Base):
    __tablename__ = "impact_assessments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    old_direction_revision_id: Mapped[str | None] = mapped_column(ForeignKey("direction_revisions.id"), nullable=True)
    new_direction_revision_id: Mapped[str] = mapped_column(ForeignKey("direction_revisions.id"))
    brief_revision_id: Mapped[str | None] = mapped_column(ForeignKey("brief_revisions.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="RUNNING")
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    result_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    started_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class AssessmentItem(Base):
    __tablename__ = "impact_assessment_items"
    __table_args__ = (UniqueConstraint("assessment_id", "asset_version_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("impact_assessments.id"), index=True)
    asset_version_id: Mapped[str] = mapped_column(ForeignKey("asset_versions.id"))
    recommendation: Mapped[str] = mapped_column(String(30))
    content: Mapped[dict] = mapped_column(JSON)
    priority: Mapped[int] = mapped_column(Integer)
    resolution_status: Mapped[str] = mapped_column(String(30), default="UNRESOLVED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AssessmentDecision(Base):
    __tablename__ = "assessment_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    assessment_item_id: Mapped[str] = mapped_column(ForeignKey("impact_assessment_items.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"))
    asset_version_id: Mapped[str] = mapped_column(ForeignKey("asset_versions.id"))
    decision_type: Mapped[str] = mapped_column(String(40))
    selected_action: Mapped[str | None]
    rationale: Mapped[str]
    previous_recommendation: Mapped[str]
    new_recommendation: Mapped[str]
    supersedes_decision_id: Mapped[str | None] = mapped_column(ForeignKey("assessment_decisions.id"), unique=True, nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
