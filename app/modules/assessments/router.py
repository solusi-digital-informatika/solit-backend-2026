from uuid import UUID
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Request, Query, Header
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.models import now
from app.modules.workspace.service import get_project, audit, paginate, direction_diff, DEMO_USER
from app.modules.assets.service import idempotent, remember
from app.modules.assessments.models import Assessment
from app.modules.assessments.schemas import *
from app.modules.assessments.service import *
import json

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]

@router.post("/projects/{project_id}/impact-assessments", status_code=201, response_model=AssessmentResponse)
def create_assessment(project_id: UUID, data: AssessmentInput, request: Request, db: DB,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=160)]):
    p = get_project(db, project_id)
    route = "/projects/" + p.id + "/impact-assessments"
    replay, digest = idempotent(db, idempotency_key, route, data)
    if replay:
        return replay
    get_project(db, p.id, lock=True)
    db.refresh(p)
    inputs = snapshot(db, p, data)
    a = Assessment(project_id=p.id, old_direction_revision_id=inputs["oldDirectionRevisionId"],
        new_direction_revision_id=inputs["newDirectionRevisionId"], brief_revision_id=inputs["briefRevisionId"],
        mode=data.mode, input_snapshot=inputs, result_metadata={"isSimulated": data.mode != "RULES_ONLY" and get_settings().ai_mode == "mock",
            "directionDiff": direction_diff(db, inputs["oldDirectionRevisionId"], inputs["newDirectionRevisionId"])}, started_by=DEMO_USER)
    db.add(a)
    db.flush()
    audit(db, request, p.id, "ASSESSMENT_STARTED", "IMPACT_ASSESSMENT", a.id, "Impact assessment started.")
    run(db, a, request)
    body = {"data": assessment_dto(db, a)}
    remember(db, idempotency_key, route, digest, body)
    db.commit()
    return body

@router.get("/projects/{project_id}/impact-assessments", response_model=AssessmentList)
def list_assessments(project_id: UUID, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 25, cursor: str | None = None,
    status: Annotated[list[AssessmentStatus] | None, Query()] = None, sort: Literal["startedAt:desc"] = "startedAt:desc"):
    p = get_project(db, project_id)
    rows = list(db.scalars(select(Assessment).where(Assessment.project_id == p.id).order_by(Assessment.started_at.desc(), Assessment.id)))
    rows = [a for a in rows if not status or a.status in status]
    return paginate(rows, limit, cursor, "assessments:" + p.id + json.dumps(status), lambda a: assessment_dto(db, a, detail=False))

@router.get("/impact-assessments/{assessment_id}", response_model=AssessmentResponse)
def assessment(assessment_id: UUID, db: DB, recommendation: Annotated[list[Recommendation] | None, Query()] = None,
    resolutionStatus: Annotated[list[ResolutionStatus] | None, Query()] = None):
    return {"data": assessment_dto(db, get_assessment(db, assessment_id), recommendation=recommendation, resolution=resolutionStatus)}

@router.post("/impact-assessments/{assessment_id}/retry", response_model=AssessmentResponse)
def retry(assessment_id: UUID, request: Request, db: DB):
    a = get_assessment(db, assessment_id, lock=True)
    if a.status != "FAILED":
        raise DomainError(409, "INVALID_STATE_TRANSITION", "Only a failed assessment can be retried.")
    a.completed_at = None
    audit(db, request, a.project_id, "ASSESSMENT_STARTED", "IMPACT_ASSESSMENT", a.id, "Impact assessment retry started.")
    run(db, a, request)
    body = {"data": assessment_dto(db, a)}
    db.commit()
    return body

@router.post("/impact-assessments/{assessment_id}/cancel", response_model=AssessmentResponse)
def cancel(assessment_id: UUID, request: Request, db: DB):
    a = get_assessment(db, assessment_id, lock=True)
    if a.status not in ("PENDING", "RUNNING"):
        raise DomainError(409, "INVALID_STATE_TRANSITION", "Only a pending or running assessment can be cancelled.")
    a.status, a.completed_at = "CANCELLED", now()
    audit(db, request, a.project_id, "ASSESSMENT_CANCELLED", "IMPACT_ASSESSMENT", a.id, "Impact assessment cancelled.")
    body = {"data": assessment_dto(db, a)}
    db.commit()
    return body

@router.post("/impact-assessments/{assessment_id}/items/{item_id}/decision", status_code=201, response_model=ResolveResponse)
def decide(assessment_id: UUID, item_id: UUID, data: ResolveInput, request: Request, db: DB):
    a = get_assessment(db, assessment_id, lock=True)
    body = {"data": resolve(db, a, item_id, data, request)}
    db.commit()
    return body
