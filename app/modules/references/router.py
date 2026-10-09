from uuid import UUID
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Request, Query, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.core.models import now
from app.core.errors import DomainError
from app.modules.workspace.service import get_project, check_timestamp, paginate, audit
from app.modules.references.models import Reference, ReferenceAttribute, ReferenceLink
from app.modules.references.schemas import *
from app.modules.references.service import *
from app.modules.references.storage import *
from app.modules.references.storage import signature as file_signature
import hmac
import time
import json

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]

@router.post("/projects/{project_id}/uploads", status_code=201, response_model=StorageResponse)
async def upload_image(project_id: UUID, request: Request, db: DB, file: UploadFile = File(...)):
    p = get_project(db, project_id)
    content = await file.read(MAX_BYTES + 1)
    await file.close()
    s = upload(db, p.id, content, file.filename)
    audit(db, request, p.id, "PROJECT_UPDATED", "STORAGE_OBJECT", s.id, "Image uploaded.")
    try:
        db.commit()
    except Exception:
        file_path(s).unlink(missing_ok=True)
        raise
    return {"data": storage_dto(s)}

@router.get("/files/{file_id}", include_in_schema=False)
def download(file_id: UUID, db: DB, expires: int, signature: str):
    if expires < int(time.time()) or not hmac.compare_digest(signature, file_signature(file_id, expires)):
        raise DomainError(403, "FORBIDDEN", "This file link is invalid or expired.")
    s = get_storage(db, file_id)
    path = file_path(s)
    if not path.is_file():
        raise DomainError(404, "NOT_FOUND", "File not found.")
    return FileResponse(path, media_type=s.mime_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

@router.get("/projects/{project_id}/references", response_model=ReferenceList)
def list_references(project_id: UUID, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 25, cursor: str | None = None,
    q: str = "", tag: str | None = None, sourceType: SourceType | None = None, attributeType: Category | None = None,
    includeArchived: bool = False, sort: Literal["createdAt:desc"] = "createdAt:desc"):
    p = get_project(db, project_id)
    rows = list(db.scalars(select(Reference).where(Reference.project_id == p.id).order_by(Reference.created_at.desc(), Reference.id)))
    selected = []
    for r in rows:
        if (r.archived_at and not includeArchived) or (tag and tag not in r.tags) or (sourceType and r.source_type != sourceType):
            continue
        attrs = list(db.scalars(select(ReferenceAttribute).where(ReferenceAttribute.reference_id == r.id)))
        if attributeType and not any(a.attribute_type == attributeType for a in attrs):
            continue
        haystack = " ".join([r.title, r.description or "", r.usage_rights_note or "", *r.tags, *[a.value["text"] for a in attrs]])
        if q.lower() in haystack.lower():
            selected.append(r)
    return paginate(selected, limit, cursor, "references:" + p.id + json.dumps([q, tag, sourceType, attributeType, includeArchived]), lambda r: reference_dto(db, r))

@router.post("/projects/{project_id}/references", status_code=201, response_model=ReferenceResponse, response_model_exclude_unset=True)
def new_reference(project_id: UUID, data: ReferenceInput, request: Request, db: DB):
    p = get_project(db, project_id, lock=True)
    if data.storageObjectId:
        get_storage(db, data.storageObjectId, p.id)
    r = Reference(project_id=p.id, title=data.title, description=data.description, source_type=data.sourceType,
        source_url=data.sourceUrl, storage_object_id=str(data.storageObjectId) if data.storageObjectId else None,
        usage_rights_note=data.usageRightsNote, tags=data.tags, added_by=DEMO_USER)
    db.add(r)
    db.flush()
    for a in data.attributes:
        add_attribute(db, r, a, request)
    for link in data.links:
        add_link(db, r, link, request)
    audit(db, request, p.id, "REFERENCE_ADDED", "REFERENCE", r.id, "Reference added.")
    db.commit()
    return {"data": reference_dto(db, r)}

@router.get("/references/{reference_id}", response_model=ReferenceResponse, response_model_exclude_unset=True)
def reference(reference_id: UUID, db: DB):
    return {"data": reference_dto(db, get_reference(db, reference_id))}

@router.patch("/references/{reference_id}", response_model=ReferenceResponse, response_model_exclude_unset=True)
def patch_reference(reference_id: UUID, data: ReferencePatch, request: Request, db: DB):
    r = get_reference(db, reference_id, lock=True)
    check_timestamp(r, data.expectedUpdatedAt)
    for source, target in [("title", "title"), ("description", "description"), ("usageRightsNote", "usage_rights_note"), ("tags", "tags")]:
        if source in data.model_fields_set:
            setattr(r, target, getattr(data, source))
    if "archived" in data.model_fields_set:
        r.archived_at = now() if data.archived else None
    r.updated_at = now()
    audit(db, request, r.project_id, "REFERENCE_UPDATED", "REFERENCE", r.id, "Reference updated.")
    db.commit()
    return {"data": reference_dto(db, r)}

@router.post("/references/{reference_id}/attributes", status_code=201, response_model=AttributeResponse)
def create_attribute(reference_id: UUID, data: AttributeInput, request: Request, db: DB):
    r = get_reference(db, reference_id, lock=True)
    a = add_attribute(db, r, data, request)
    db.commit()
    return {"data": attribute_dto(db, a)}

@router.patch("/reference-attributes/{attribute_id}", response_model=AttributeResponse)
def update_attribute(attribute_id: UUID, data: AttributePatch, request: Request, db: DB):
    a = db.get(ReferenceAttribute, str(attribute_id))
    if not a:
        raise DomainError(404, "NOT_FOUND", "Reference attribute not found.")
    r = get_reference(db, a.reference_id, lock=True)
    db.refresh(a)
    if data.value:
        a.value = data.value.model_dump(mode="json")
        a.review_status = data.reviewStatus or "CORRECTED"
    elif data.reviewStatus:
        a.review_status = data.reviewStatus
    a.updated_at, r.updated_at = now(), now()
    audit(db, request, r.project_id, "REFERENCE_ATTRIBUTE_REVIEWED", "REFERENCE_ATTRIBUTE", a.id, "Reference attribute reviewed.")
    db.commit()
    return {"data": attribute_dto(db, a)}

@router.post("/references/{reference_id}/links", status_code=201, response_model=ReferenceResponse, response_model_exclude_unset=True)
def link(reference_id: UUID, data: LinkInput, request: Request, db: DB):
    r = get_reference(db, reference_id, lock=True)
    add_link(db, r, data, request)
    db.commit()
    return {"data": reference_dto(db, r)}

@router.delete("/references/{reference_id}/links", response_model=ReferenceResponse, response_model_exclude_unset=True)
def unlink(reference_id: UUID, targetType: TargetType, targetId: UUID, request: Request, db: DB):
    r = get_reference(db, reference_id, lock=True)
    for item in links(db, r):
        if item.target_type == targetType and item.target_id == str(targetId):
            db.delete(item)
    r.updated_at = now()
    audit(db, request, r.project_id, "REFERENCE_LINKED", "REFERENCE", r.id, "Reference link removed.")
    db.commit()
    return {"data": reference_dto(db, r)}

@router.post("/references/{reference_id}/analyze", response_model=ReferenceResponse, response_model_exclude_unset=True)
def analyze(reference_id: UUID, request: Request, db: DB):
    r = get_reference(db, reference_id, lock=True)
    if not r.storage_object_id or not file_path(get_storage(db, r.storage_object_id)).is_file():
        raise DomainError(422, "NO_IMAGE_INPUT", "No image bytes are available for analysis.")
    simulated = get_settings().ai_mode == "mock"
    if simulated:
        # Deterministic simulated output, never represented as a visual observation.
        attributes = [AttributeInput(attributeType="MOOD", value={"text": "Simulated demo mood"})]
    else:
        from app.modules.references.provider import analyze_image
        attributes = analyze_image(file_path(get_storage(db, r.storage_object_id)))
    for attribute in attributes:
        add_attribute(db, r, attribute, request, origin="AI_INFERRED")
    db.commit()
    return {"data": reference_dto(db, r), "meta": {"simulated": simulated}}
