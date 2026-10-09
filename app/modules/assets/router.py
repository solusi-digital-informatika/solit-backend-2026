from uuid import UUID
from typing import Annotated, Literal
from fastapi import APIRouter, Request, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.models import DirectionRevision, Direction, now
from app.core.errors import DomainError
from app.modules.workspace.service import get_project, audit, check_timestamp, paginate, DEMO_USER
from app.modules.assets.models import Asset, AssetVersion, VersionDecision
from app.modules.assets.schemas import *
from app.modules.assets.service import *
import json

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]
Key = Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=160)]

@router.get("/projects/{project_id}/assets", response_model=AssetList)
def list_assets(project_id: UUID, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 25, cursor: str | None = None,
    q: str = "", status: Annotated[list[AssetStatus] | None, Query()] = None,
    assetType: Annotated[list[AssetType] | None, Query()] = None, directionId: UUID | None = None,
    tag: str | None = None, ownerUserId: UUID | None = None, includeArchived: bool = False,
    sort: Literal["updatedAt:desc", "title:asc", "status:asc", "versionCount:desc"] = "updatedAt:desc"):
    p = get_project(db, project_id)
    rows = list(db.scalars(select(Asset).where(Asset.project_id == p.id)))
    selected = []
    for a in rows:
        if (not includeArchived and a.status == "ARCHIVED") or (status and a.status not in status) or (assetType and a.asset_type not in assetType):
            continue
        if tag and tag not in a.tags or ownerUserId and str(ownerUserId) != a.owner_user_id:
            continue
        if q.lower() not in (a.title + " " + (a.description or "") + " " + " ".join(a.tags)).lower():
            continue
        latest = versions(db, a)
        if directionId:
            revision = db.get(DirectionRevision, latest[0].direction_revision_id) if latest and latest[0].direction_revision_id else None
            if not revision or revision.direction_id != str(directionId):
                continue
        selected.append(a)
    key = {"updatedAt:desc": lambda a: (iso(a.updated_at), a.id),
        "title:asc": lambda a: (a.title.lower(), a.id), "status:asc": lambda a: (a.status, a.id),
        "versionCount:desc": lambda a: (len(versions(db, a)), a.id)}[sort]
    selected.sort(key=key, reverse=sort.endswith(":desc"))
    scope = "assets:" + str(project_id) + json.dumps([q, status, assetType, str(directionId), tag, str(ownerUserId), includeArchived, sort])
    return paginate(selected, limit, cursor, scope, lambda a: asset_dto(db, a))

@router.post("/projects/{project_id}/assets", status_code=201, response_model=AssetDetailResponse)
def new_asset(project_id: UUID, data: AssetInput, request: Request, db: DB, idempotency_key: Key = None):
    p = get_project(db, project_id)
    route = "/projects/" + p.id + "/assets"
    replay, digest = idempotent(db, idempotency_key, route, data)
    if replay:
        return replay
    get_project(db, p.id, lock=True)
    a = Asset(project_id=p.id, title=data.title, description=data.description, asset_type=data.assetType,
        tags=list(dict.fromkeys(x.strip() for x in data.tags if x.strip())), owner_user_id=owner_id(db, data.ownerUserId), created_by=DEMO_USER)
    db.add(a)
    db.flush()
    audit(db, request, p.id, "ASSET_CREATED", "ASSET", a.id, "Asset registered.")
    if data.initialVersion:
        create_version(db, a, data.initialVersion, request)
    body = {"data": asset_dto(db, a, detail=True)}
    remember(db, idempotency_key, route, digest, body)
    db.commit()
    return body

@router.get("/assets/{asset_id}", response_model=AssetDetailResponse)
def asset(asset_id: UUID, db: DB):
    return {"data": asset_dto(db, get_asset(db, asset_id), detail=True)}

@router.patch("/assets/{asset_id}", response_model=AssetResponse)
def patch_asset(asset_id: UUID, data: AssetPatch, request: Request, db: DB):
    a = get_asset(db, asset_id, lock=True)
    check_timestamp(a, data.expectedUpdatedAt)
    if data.status == "APPROVED":
        raise DomainError(409, "INVALID_STATE_TRANSITION", "Approve a specific asset version instead.")
    for source, target in [("title", "title"), ("description", "description"), ("assetType", "asset_type"), ("status", "status"), ("tags", "tags")]:
        if source in data.model_fields_set:
            setattr(a, target, getattr(data, source))
    if "ownerUserId" in data.model_fields_set:
        a.owner_user_id = owner_id(db, data.ownerUserId)
    if "status" in data.model_fields_set:
        a.archived_at = now() if data.status == "ARCHIVED" else None
    a.updated_at = now()
    audit(db, request, a.project_id, "ASSET_UPDATED", "ASSET", a.id, "Asset metadata updated.")
    db.commit()
    return {"data": asset_dto(db, a)}

@router.get("/assets/{asset_id}/versions", response_model=VersionList)
def list_versions(asset_id: UUID, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 25, cursor: str | None = None):
    a = get_asset(db, asset_id)
    return paginate(versions(db, a), limit, cursor, "asset-versions:" + a.id, summary)

@router.post("/assets/{asset_id}/versions", status_code=201, response_model=VersionResponse)
def new_version(asset_id: UUID, data: VersionInput, request: Request, db: DB, idempotency_key: Key = None):
    a = get_asset(db, asset_id)
    route = "/assets/" + a.id + "/versions"
    replay, digest = idempotent(db, idempotency_key, route, data)
    if replay:
        return replay
    v = create_version(db, a, data, request)
    body = {"data": version_dto(db, v)}
    remember(db, idempotency_key, route, digest, body)
    db.commit()
    return body

@router.get("/asset-versions/{version_id}", response_model=VersionResponse)
def version(version_id: UUID, db: DB):
    return {"data": version_dto(db, get_version(db, version_id))}

@router.patch("/asset-versions/{version_id}", response_model=VersionResponse)
def patch_version(version_id: UUID, data: VersionPatch, request: Request, db: DB):
    v = get_version(db, version_id)
    a = get_asset(db, v.asset_id, lock=True)
    db.refresh(v)
    allowed = {"DRAFT": {"CANDIDATE", "SUPERSEDED"}, "CANDIDATE": {"DRAFT", "NEEDS_REVIEW", "SUPERSEDED"},
        "NEEDS_REVIEW": {"CANDIDATE", "SUPERSEDED"}, "APPROVED": {"SUPERSEDED"}, "REJECTED": {"SUPERSEDED"}, "SUPERSEDED": set()}
    if data.status not in allowed[v.status]:
        raise DomainError(409, "INVALID_STATE_TRANSITION", "This version status transition is not allowed.")
    v.status = data.status
    a.updated_at = now()
    audit(db, request, a.project_id, "ASSET_VERSION_STATUS_CHANGED", "ASSET_VERSION", v.id, data.reason)
    db.commit()
    return {"data": version_dto(db, v)}

@router.post("/asset-versions/{version_id}/decisions", status_code=201, response_model=VersionDecisionResponse)
def decide_version(version_id: UUID, data: DecisionInput, request: Request, db: DB):
    v = get_version(db, version_id)
    a = get_asset(db, v.asset_id, lock=True)
    db.refresh(v)
    v.status = {"APPROVE_VERSION": "APPROVED", "REJECT_VERSION": "REJECTED", "REQUEST_REVISION": "NEEDS_REVIEW"}[data.decisionType]
    if data.decisionType == "APPROVE_VERSION":
        a.status = "APPROVED"
        a.archived_at = None
    a.updated_at = now()
    d = VersionDecision(project_id=a.project_id, asset_id=a.id, asset_version_id=v.id,
        decision_type=data.decisionType, rationale=data.rationale, created_by=DEMO_USER)
    db.add(d)
    db.flush()
    audit(db, request, a.project_id, "ASSET_VERSION_STATUS_CHANGED", "ASSET_VERSION", v.id, data.rationale)
    body = {"data": {"decision": decision_dto(db, d), "assetVersion": version_dto(db, v)}}
    db.commit()
    return body
