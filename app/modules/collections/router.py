from uuid import UUID
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Request, Query, Header
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.models import now
from app.core.errors import DomainError, invalid
from app.modules.workspace.service import get_project, audit, actor, iso, check_timestamp, paginate, DEMO_USER
from app.modules.assets.service import idempotent, remember
from app.modules.collections.models import Collection, CollectionRevision, CollectionItem, Approval
from app.modules.collections.schemas import *
from app.modules.collections.service import *
import json

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]
Key = Annotated[str | None, Header(alias="Idempotency-Key", min_length=8, max_length=160)]
Limit = Annotated[int, Query(ge=1, le=100)]

@router.get("/projects/{project_id}/collections", response_model=CollectionList)
def list_collections(project_id: UUID, db: DB, limit: Limit = 25, cursor: str | None = None,
    status: Annotated[list[CollectionStatus] | None, Query()] = None, collectionType: CollectionType | None = None, includeArchived: bool = False):
    p = get_project(db, project_id)
    rows = list(db.scalars(select(Collection).where(Collection.project_id == p.id).order_by(Collection.created_at.desc(), Collection.id)))
    rows = [c for c in rows if (includeArchived or c.status != "ARCHIVED") and (not status or c.status in status) and (not collectionType or c.collection_type == collectionType)]
    return paginate(rows, limit, cursor, "collections:" + p.id + json.dumps([status, collectionType, includeArchived]), lambda c: collection_dto(db, c))

@router.post("/projects/{project_id}/collections", status_code=201, response_model=DetailResponse)
def create_collection(project_id: UUID, data: CollectionInput, request: Request, db: DB, idempotency_key: Key = None):
    p = get_project(db, project_id)
    route = "/projects/" + p.id + "/collections"
    replay, digest = idempotent(db, idempotency_key, route, data)
    if replay:
        return replay
    get_project(db, p.id, lock=True)
    c = Collection(project_id=p.id, name=data.name, description=data.description, collection_type=data.collectionType, created_by=DEMO_USER)
    db.add(c)
    db.flush()
    r = CollectionRevision(collection_id=c.id, revision_number=1, change_summary=None, created_by=DEMO_USER)
    db.add(r)
    db.flush()
    audit(db, request, p.id, "COLLECTION_CREATED", "COLLECTION", c.id, "Collection created.")
    audit(db, request, p.id, "COLLECTION_REVISION_CREATED", "COLLECTION_REVISION", r.id, "Collection revision created.")
    for entry in data.items:
        add_item(db, c, r, entry, request)
    body = {"data": collection_dto(db, c, detail=True)}
    remember(db, idempotency_key, route, digest, body)
    db.commit()
    return body

@router.get("/collections/{collection_id}", response_model=DetailResponse)
def collection(collection_id: UUID, db: DB):
    return {"data": collection_dto(db, get_collection(db, collection_id), detail=True)}

@router.patch("/collections/{collection_id}", response_model=CollectionResponse)
def patch_collection(collection_id: UUID, data: CollectionPatch, request: Request, db: DB):
    c = get_collection(db, collection_id, lock=True)
    check_timestamp(c, data.expectedUpdatedAt)
    for field in ("name", "description"):
        if field in data.model_fields_set:
            setattr(c, field, getattr(data, field))
    if "archived" in data.model_fields_set:
        c.archived_at = now() if data.archived else None
        if data.archived:
            c.status = "ARCHIVED"
        else:
            rows = revisions(db, c)
            c.status = "APPROVED" if any(r.status == "APPROVED" for r in rows) else ("IN_REVIEW" if rows[0].status == "IN_REVIEW" else "DRAFT")
    c.updated_at = now()
    audit(db, request, c.project_id, "PROJECT_UPDATED", "COLLECTION", c.id, "Collection metadata updated.")
    db.commit()
    return {"data": collection_dto(db, c)}

@router.get("/collections/{collection_id}/revisions", response_model=RevisionList)
def list_revisions(collection_id: UUID, db: DB, limit: Limit = 25, cursor: str | None = None):
    c = get_collection(db, collection_id)
    return paginate(revisions(db, c), limit, cursor, "collection-revisions:" + c.id, lambda r: revision_dto(db, r))

@router.get("/collection-revisions/{revision_id}", response_model=RevisionResponse)
def revision(revision_id: UUID, db: DB):
    return {"data": revision_dto(db, get_revision(db, revision_id), detail=True)}

@router.post("/collections/{collection_id}/revisions", status_code=201, response_model=RevisionResponse)
def create_revision(collection_id: UUID, data: RevisionInput, request: Request, db: DB, idempotency_key: Key = None):
    c = get_collection(db, collection_id)
    route = "/collections/" + c.id + "/revisions"
    replay, digest = idempotent(db, idempotency_key, route, data)
    if replay:
        return replay
    get_collection(db, c.id, lock=True)
    rows = revisions(db, c)
    if any(r.status in ("DRAFT", "IN_REVIEW") for r in rows):
        raise DomainError(409, "OPEN_REVISION_EXISTS", "An open collection revision already exists.")
    base = get_revision(db, data.basedOnRevisionId) if data.basedOnRevisionId else rows[0]
    if base.collection_id != c.id:
        base_collection = get_collection(db, base.collection_id)
        if base_collection.project_id != c.project_id:
            raise DomainError(422, "CROSS_PROJECT_REFERENCE", "The base revision belongs to another project.")
        invalid("The base revision must belong to this collection.", "body.basedOnRevisionId")
    r = CollectionRevision(collection_id=c.id, revision_number=rows[0].revision_number + 1, change_summary=data.changeSummary, created_by=DEMO_USER)
    db.add(r)
    db.flush()
    for item in items(db, base):
        db.add(CollectionItem(collection_revision_id=r.id, asset_id=item.asset_id, asset_version_id=item.asset_version_id,
            position=item.position, role=item.role, note=item.note))
    c.updated_at = now()
    if c.status not in ("APPROVED", "ARCHIVED"):
        c.status = "DRAFT"
    db.flush()
    audit(db, request, c.project_id, "COLLECTION_REVISION_CREATED", "COLLECTION_REVISION", r.id, "Collection revision created with existing pins.")
    body = {"data": revision_dto(db, r, detail=True)}
    remember(db, idempotency_key, route, digest, body)
    db.commit()
    return body

@router.post("/collection-revisions/{revision_id}/items", status_code=201, response_model=ItemResponse)
def pin(revision_id: UUID, data: ItemInput, request: Request, db: DB):
    r = get_revision(db, revision_id, lock=True)
    c = get_collection(db, r.collection_id)
    item = add_item(db, c, r, data, request)
    body = {"data": item_dto(db, item)}
    db.commit()
    return body

@router.patch("/collection-revision-items/{item_id}", response_model=ItemResponse)
def update_item(item_id: UUID, data: ItemPatch, request: Request, db: DB):
    item = get_item(db, item_id)
    r = get_revision(db, item.collection_revision_id)
    c = get_collection(db, r.collection_id)
    frozen(r)
    if data.assetVersionId and str(data.assetVersionId) != item.asset_version_id:
        v = checked_version(db, c, data.assetVersionId)
        if v.asset_id != item.asset_id:
            invalid("Replacement must be a version of the same asset.", "body.assetVersionId")
        if not data.rationale or len(data.rationale) < 5:
            invalid("A replacement rationale of at least 5 characters is required.", "body.rationale")
        item.asset_version_id = v.id
        pin_decision(db, c, r, v, request, "REPLACE_PINNED_VERSION", data.rationale)
    for field in ("role", "note"):
        if field in data.model_fields_set:
            setattr(item, field, getattr(data, field))
    if data.position:
        rows = items(db, r)
        if data.position > len(rows):
            invalid("Position must be within the collection.", "body.position")
        rows.remove(item)
        rows.insert(data.position - 1, item)
        arrange(db, rows)
    c.updated_at = now()
    audit(db, request, c.project_id, "PROJECT_UPDATED", "COLLECTION_ITEM", item.id, "Collection draft item updated.")
    db.flush()
    body = {"data": item_dto(db, item)}
    db.commit()
    return body

@router.delete("/collection-revision-items/{item_id}", response_model=RevisionResponse)
def remove_item(item_id: UUID, request: Request, db: DB):
    item = get_item(db, item_id)
    r = get_revision(db, item.collection_revision_id)
    c = get_collection(db, r.collection_id)
    frozen(r)
    db.delete(item)
    db.flush()
    arrange(db, items(db, r))
    c.updated_at = now()
    audit(db, request, c.project_id, "PROJECT_UPDATED", "COLLECTION_REVISION", r.id, "Collection draft item removed.")
    body = {"data": revision_dto(db, r, detail=True)}
    db.commit()
    return body

@router.post("/collection-revisions/{revision_id}/submit-review", response_model=RevisionResponse)
def submit(revision_id: UUID, request: Request, db: DB):
    r = get_revision(db, revision_id, lock=True)
    c = get_collection(db, r.collection_id)
    if r.status != "DRAFT":
        raise DomainError(409, "INVALID_STATE_TRANSITION", "Only a draft revision can be submitted.")
    if not items(db, r):
        invalid("A collection needs at least one item before review.")
    r.status, r.submitted_at = "IN_REVIEW", now()
    if c.status not in ("APPROVED", "ARCHIVED"):
        c.status = "IN_REVIEW"
    c.updated_at = now()
    audit(db, request, c.project_id, "COLLECTION_SUBMITTED", "COLLECTION_REVISION", r.id, "Collection submitted for review.")
    db.commit()
    return {"data": revision_dto(db, r, detail=True)}

def review(db, r, c, request, decision, comment):
    if r.status != "IN_REVIEW":
        raise DomainError(409, "INVALID_STATE_TRANSITION", "Only an in-review revision can be reviewed.")
    if decision == "APPROVED":
        for previous in revisions(db, c):
            if previous.status == "APPROVED":
                previous.status = "SUPERSEDED"
        r.status, r.approved_at = "APPROVED", now()
        if c.status != "ARCHIVED":
            c.status = "APPROVED"
    else:
        r.status = "REJECTED"
        if c.status not in ("APPROVED", "ARCHIVED"):
            c.status = "DRAFT"
    c.updated_at = now()
    db.add(Approval(collection_revision_id=r.id, decision=decision, comment=comment, reviewed_by=DEMO_USER))
    audit(db, request, c.project_id, "COLLECTION_APPROVED" if decision == "APPROVED" else "COLLECTION_REVIEW_REJECTED",
        "COLLECTION_REVISION", r.id, "Collection review recorded.")
    db.flush()

@router.post("/collection-revisions/{revision_id}/approve", response_model=RevisionResponse)
def approve(revision_id: UUID, data: ApproveInput, request: Request, db: DB):
    r = get_revision(db, revision_id, lock=True)
    review(db, r, get_collection(db, r.collection_id), request, "APPROVED", data.comment)
    db.commit()
    return {"data": revision_dto(db, r, detail=True)}

@router.post("/collection-revisions/{revision_id}/reject", response_model=RevisionResponse)
def reject(revision_id: UUID, data: RejectInput, request: Request, db: DB):
    r = get_revision(db, revision_id, lock=True)
    review(db, r, get_collection(db, r.collection_id), request, data.decision, data.comment)
    db.commit()
    return {"data": revision_dto(db, r, detail=True)}
