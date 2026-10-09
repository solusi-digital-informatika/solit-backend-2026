from uuid import UUID
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query, Request, Header
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.errors import DomainError, invalid
from app.core.models import Project, BriefRevision, Direction, DirectionRevision, IdempotencyRecord, now
from app.core.seed import seed_workspace
from app.modules.workspace.schemas import *
from app.modules.workspace.service import *
import hashlib
import json

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=100)]

@router.get("/projects", response_model=ProjectList)
def projects(db: DB, limit: Limit = 25, cursor: str | None = None,
             status: Literal["ACTIVE", "ARCHIVED"] | None = None, q: str = "",
             sort: Literal["updatedAt:desc", "name:asc"] = "updatedAt:desc"):
    rows = list(db.scalars(select(Project).where(Project.owner_user_id == DEMO_USER)))
    rows = [p for p in rows if (not status or p.status == status) and q.lower() in (p.name + " " + (p.description or "")).lower()]
    rows.sort(key=lambda p: ((iso(p.updated_at), p.id) if sort == "updatedAt:desc" else (p.name.lower(), p.id)), reverse=sort == "updatedAt:desc")
    return paginate(rows, limit, cursor, "projects:" + json.dumps([status, q, sort]), lambda p: project_dto(db, p))

@router.post("/projects", status_code=201, response_model=ProjectResponse)
def new_project(data: ProjectInput, request: Request, db: DB, idempotency_key: Annotated[str | None, Header(min_length=8, max_length=160)] = None):
    # Lock actor row to serialize idempotency lookup + slug allocation across API workers.
    db.scalar(select(User).where(User.id == DEMO_USER).with_for_update())
    digest = hashlib.sha256(json.dumps(data.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    if idempotency_key:
        existing = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.actor_id == DEMO_USER,
            IdempotencyRecord.route == "/projects", IdempotencyRecord.key == idempotency_key))
        if existing:
            if existing.request_hash != digest:
                raise DomainError(422, "IDEMPOTENCY_KEY_REUSED", "This idempotency key was used with different input.")
            return JSONResponse(existing.response_body, headers={"Idempotent-Replayed": "true"})
    p = seed_workspace(db, request, fixed=False, name=data.name, description=data.description) if data.template == "DEMO_SOLARA" else create_project(db, data, request)
    result = {"data": project_dto(db, p)}
    if idempotency_key:
        db.add(IdempotencyRecord(actor_id=DEMO_USER, route="/projects", key=idempotency_key, request_hash=digest, response_body=result))
    db.commit()
    return result

@router.get("/projects/{project_id}", response_model=ProjectResponse)
def project(project_id: UUID, db: DB):
    return {"data": project_dto(db, get_project(db, project_id))}

@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def patch_project(project_id: UUID, data: ProjectPatch, request: Request, db: DB):
    p = get_project(db, project_id, lock=True)
    check_timestamp(p, data.expectedUpdatedAt)
    for field in ("name", "description"):
        if field in data.model_fields_set:
            setattr(p, field, getattr(data, field))
    p.updated_at = now()
    audit(db, request, p.id, "PROJECT_UPDATED", "PROJECT", p.id, "Project updated.")
    db.commit()
    return {"data": project_dto(db, p)}

@router.delete("/projects/{project_id}", response_model=ProjectResponse)
def archive_project(project_id: UUID, request: Request, db: DB, confirm: bool = False):
    if not confirm:
        invalid("Confirmation is required.", "query.confirm")
    p = get_project(db, project_id, lock=True)
    p.status, p.archived_at, p.updated_at = "ARCHIVED", now(), now()
    audit(db, request, p.id, "PROJECT_ARCHIVED", "PROJECT", p.id, "Project archived.")
    db.commit()
    return {"data": project_dto(db, p)}

@router.post("/projects/{project_id}/restore", response_model=ProjectResponse)
def restore_project(project_id: UUID, request: Request, db: DB):
    p = get_project(db, project_id, lock=True)
    p.status, p.archived_at, p.updated_at = "ACTIVE", None, now()
    audit(db, request, p.id, "PROJECT_RESTORED", "PROJECT", p.id, "Project restored.")
    db.commit()
    return {"data": project_dto(db, p)}

@router.get("/projects/{project_id}/brief-revisions", response_model=BriefList)
def briefs(project_id: UUID, db: DB, limit: Limit = 25, cursor: str | None = None, sort: Literal["revisionNumber:desc"] = "revisionNumber:desc"):
    p = get_project(db, project_id)
    rows = list(db.scalars(select(BriefRevision).where(BriefRevision.project_id == p.id).order_by(BriefRevision.revision_number.desc())))
    return paginate(rows, limit, cursor, "briefs:" + p.id, lambda r: revision_dto(db, r))

@router.get("/projects/{project_id}/brief-revisions/latest", response_model=BriefResponse)
def latest_brief(project_id: UUID, db: DB):
    p = get_project(db, project_id)
    r = db.scalar(select(BriefRevision).where(BriefRevision.project_id == p.id).order_by(BriefRevision.revision_number.desc()))
    if r is None:
        raise DomainError(404, "NOT_FOUND", "No brief revision exists.")
    return {"data": revision_dto(db, r)}

@router.get("/brief-revisions/{revision_id}", response_model=BriefResponse)
def brief(revision_id: UUID, db: DB):
    return {"data": revision_dto(db, owned(db, BriefRevision, revision_id))}

@router.post("/projects/{project_id}/brief-revisions", status_code=201, response_model=BriefResponse)
def new_brief(project_id: UUID, data: BriefInput, request: Request, db: DB):
    r = save_brief(db, get_project(db, project_id, lock=True), data, request)
    db.commit()
    return {"data": revision_dto(db, r)}

@router.get("/projects/{project_id}/directions", response_model=DirectionList)
def directions(project_id: UUID, db: DB, limit: Limit = 25, cursor: str | None = None,
               status: Annotated[list[Literal["DRAFT", "ACTIVE", "SUPERSEDED", "ARCHIVED"]] | None, Query()] = None, includeArchived: bool = False):
    p = get_project(db, project_id)
    rows = list(db.scalars(select(Direction).where(Direction.project_id == p.id).order_by(Direction.created_at.desc(), Direction.id)))
    rows = [d for d in rows if (includeArchived or d.status != "ARCHIVED") and (not status or d.status in status)]
    return paginate(rows, limit, cursor, "directions:" + p.id + json.dumps([status, includeArchived]), lambda d: direction_dto(db, d))

@router.post("/projects/{project_id}/directions", status_code=201, response_model=DirectionResponse)
def new_direction(project_id: UUID, data: DirectionInput, request: Request, db: DB):
    d = save_direction(db, get_project(db, project_id, lock=True), data, request)
    db.commit()
    return {"data": direction_dto(db, d)}

@router.get("/directions/{direction_id}", response_model=DirectionResponse)
def direction(direction_id: UUID, db: DB):
    return {"data": direction_dto(db, owned(db, Direction, direction_id))}

@router.patch("/directions/{direction_id}", response_model=DirectionResponse)
def patch_direction(direction_id: UUID, data: DirectionPatch, request: Request, db: DB):
    d = owned(db, Direction, direction_id)
    p = get_project(db, d.project_id, lock=True)
    db.refresh(d)
    check_timestamp(d, data.expectedUpdatedAt)
    if "status" in data.model_fields_set:
        if data.status is None:
            invalid("status cannot be null", "body.status")
        if d.status == "ACTIVE" or (data.status == "DRAFT" and d.status != "ARCHIVED"):
            raise DomainError(409, "INVALID_STATE_TRANSITION", "This direction status change is not allowed.")
        d.status = data.status
        d.archived_at = now() if data.status == "ARCHIVED" else None
    for field in ("name", "description"):
        if field in data.model_fields_set:
            setattr(d, field, getattr(data, field))
    d.updated_at = now()
    audit(db, request, p.id, "PROJECT_UPDATED", "DIRECTION", d.id, "Direction metadata updated.")
    db.commit()
    return {"data": direction_dto(db, d)}

@router.get("/directions/{direction_id}/revisions", response_model=RevisionList)
def direction_revisions(direction_id: UUID, db: DB, limit: Limit = 25, cursor: str | None = None):
    d = owned(db, Direction, direction_id)
    rows = list(db.scalars(select(DirectionRevision).where(DirectionRevision.direction_id == d.id).order_by(DirectionRevision.revision_number.desc())))
    return paginate(rows, limit, cursor, "revisions:" + d.id, lambda r: revision_dto(db, r))

@router.post("/directions/{direction_id}/revisions", status_code=201, response_model=RevisionResponse)
def new_direction_revision(direction_id: UUID, data: RevisionInput, request: Request, db: DB):
    d = owned(db, Direction, direction_id)
    get_project(db, d.project_id, lock=True)
    db.refresh(d)
    if not data.changeSummary:
        invalid("A change summary is required.", "body.changeSummary")
    count = len(list(db.scalars(select(DirectionRevision).where(DirectionRevision.direction_id == d.id))))
    r = DirectionRevision(direction_id=d.id, revision_number=count + 1, content=data.model_dump(mode="json"), created_by=DEMO_USER)
    db.add(r)
    d.updated_at = now()
    db.flush()
    audit(db, request, d.project_id, "DIRECTION_REVISION_CREATED", "DIRECTION_REVISION", r.id, "Direction revision created.")
    db.commit()
    return {"data": revision_dto(db, r)}

@router.get("/direction-revisions/diff")
def diff(db: DB, to: UUID, from_revision: Annotated[UUID | None, Query(alias="from")] = None):
    return {"data": direction_diff(db, from_revision, to)}

@router.get("/direction-revisions/{revision_id}", response_model=RevisionResponse)
def direction_revision(revision_id: UUID, db: DB):
    return {"data": revision_dto(db, owned(db, DirectionRevision, revision_id))}

@router.post("/projects/{project_id}/active-direction")
def active_direction(project_id: UUID, data: ActivateInput, request: Request, db: DB):
    result = activate(db, get_project(db, project_id, lock=True), data.directionRevisionId, data.reason, request)
    db.commit()
    return {"data": result}
