from uuid import UUID
from datetime import datetime
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.models import Decision, Activity
from app.modules.workspace.service import get_project, actor, iso, paginate
from app.modules.assets.models import VersionDecision
from app.modules.assets.service import decision_dto as version_decision_dto
from app.modules.collections.models import PinDecision
from app.modules.assessments.models import AssessmentItem, AssessmentDecision
from app.modules.assessments.service import decision_dto
from app.modules.assessments.schemas import DecisionList
import json

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]
DecisionType = Literal["ACCEPT_RECOMMENDATION", "OVERRIDE_RECOMMENDATION", "DISMISS_RECOMMENDATION", "DEFER_RECOMMENDATION",
    "APPROVE_VERSION", "REJECT_VERSION", "REQUEST_REVISION", "CHANGE_DIRECTION", "PIN_TO_COLLECTION", "REPLACE_PINNED_VERSION"]

@router.get("/projects/{project_id}/decisions", response_model=DecisionList)
def decisions(project_id: UUID, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 25, cursor: str | None = None,
    assessmentId: UUID | None = None, assetId: UUID | None = None, assetVersionId: UUID | None = None,
    collectionRevisionId: UUID | None = None, decisionType: Annotated[list[DecisionType] | None, Query()] = None,
    currentOnly: bool = False, sort: Literal["createdAt:desc"] = "createdAt:desc"):
    p = get_project(db, project_id)
    result = []
    for d in db.scalars(select(Decision).where(Decision.project_id == p.id)):
        result.append({"id": d.id, "projectId": p.id, "assessmentItemId": None, "assetId": None, "assetVersionId": None,
            "directionRevisionId": d.direction_revision_id, "collectionRevisionId": None, "decisionType": "CHANGE_DIRECTION",
            "selectedAction": None, "rationale": d.rationale, "previousRecommendation": None, "newRecommendation": None,
            "supersedesDecisionId": None, "supersededByDecisionId": None, "createdBy": actor(db), "createdAt": iso(d.created_at)})
    for cls in (VersionDecision, PinDecision):
        result.extend(version_decision_dto(db, d) for d in db.scalars(select(cls).where(cls.project_id == p.id)))
    result.extend(decision_dto(db, d) for d in db.scalars(select(AssessmentDecision).where(AssessmentDecision.project_id == p.id)))
    if assessmentId:
        item_ids = set(db.scalars(select(AssessmentItem.id).where(AssessmentItem.assessment_id == str(assessmentId))))
        result = [d for d in result if d["assessmentItemId"] in item_ids]
    for key, expected in [("assetId", assetId), ("assetVersionId", assetVersionId), ("collectionRevisionId", collectionRevisionId)]:
        if expected:
            result = [d for d in result if d[key] == str(expected)]
    if decisionType:
        result = [d for d in result if d["decisionType"] in decisionType]
    if currentOnly:
        result = [d for d in result if d["supersededByDecisionId"] is None]
    result.sort(key=lambda d: (d["createdAt"], d["id"]), reverse=True)
    scope = "decisions:" + p.id + json.dumps([str(assessmentId), str(assetId), str(assetVersionId), str(collectionRevisionId), decisionType, currentOnly])
    return paginate(result, limit, cursor, scope, lambda d: d)

@router.get("/projects/{project_id}/activity")
def activity(project_id: UUID, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 25, cursor: str | None = None,
    from_date: Annotated[datetime | None, Query(alias="from")] = None, to: datetime | None = None,
    actorId: UUID | None = None, eventType: Annotated[list[str] | None, Query()] = None, entityType: str | None = None,
    entityId: UUID | None = None, sort: Literal["createdAt:desc"] = "createdAt:desc"):
    p = get_project(db, project_id)
    rows = list(db.scalars(select(Activity).where(Activity.project_id == p.id).order_by(Activity.created_at.desc(), Activity.id.desc())))
    rows = [a for a in rows if (not from_date or iso(a.created_at) >= iso(from_date)) and (not to or iso(a.created_at) <= iso(to))
        and (not actorId or a.actor_user_id == str(actorId)) and (not eventType or a.event_type in eventType)
        and (not entityType or a.entity_type == entityType) and (not entityId or a.entity_id == str(entityId))]
    scope = "activity:" + p.id + json.dumps([iso(from_date), iso(to), str(actorId), eventType, entityType, str(entityId)])
    def dto(a):
        return {"id": a.id, "projectId": p.id, "actor": actor(db), "eventType": a.event_type,
            "entityType": a.entity_type, "entityId": a.entity_id, "summary": a.summary, "metadata": {},
            "requestId": a.request_id, "createdAt": iso(a.created_at)}
    return paginate(rows, limit, cursor, scope, dto)
