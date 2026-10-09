from sqlalchemy import select
from app.core.errors import DomainError
from app.core.models import now, DirectionRevision
from app.modules.workspace.service import get_project, owned, audit, actor, iso, DEMO_USER
from app.modules.assets.service import get_asset, get_version
from app.modules.references.models import Reference, ReferenceAttribute, ReferenceLink
from app.modules.references.storage import get_storage, file_url

def get_reference(db, identifier, project_id=None, lock=False):
    r = db.get(Reference, str(identifier))
    if r is None:
        raise DomainError(404, "NOT_FOUND", "Reference not found.")
    get_project(db, r.project_id, lock=lock)
    if lock:
        db.refresh(r)
    if project_id is not None and r.project_id != project_id:
        raise DomainError(422, "CROSS_PROJECT_REFERENCE", "The reference belongs to another project.")
    return r

def attribute_dto(db, a):
    return {"id": a.id, "referenceId": a.reference_id, "attributeType": a.attribute_type,
        "value": a.value, "origin": a.origin, "reviewStatus": a.review_status,
        "createdBy": actor(db) if a.created_by else None, "createdAt": iso(a.created_at), "updatedAt": iso(a.updated_at)}

def links(db, r):
    return list(db.scalars(select(ReferenceLink).where(ReferenceLink.reference_id == r.id).order_by(ReferenceLink.id)))

def reference_dto(db, r):
    attributes = list(db.scalars(select(ReferenceAttribute).where(ReferenceAttribute.reference_id == r.id).order_by(ReferenceAttribute.created_at, ReferenceAttribute.id)))
    url = file_url(get_storage(db, r.storage_object_id)) if r.storage_object_id else r.source_url
    return {"id": r.id, "projectId": r.project_id, "title": r.title, "description": r.description,
        "sourceType": r.source_type, "sourceUrl": r.source_url, "usageRightsNote": r.usage_rights_note,
        "tags": r.tags, "fileUrl": url, "thumbnailUrl": url, "attributes": [attribute_dto(db, a) for a in attributes],
        "links": [{"targetType": x.target_type, "targetId": x.target_id, "relationshipType": x.relationship_type or None, "usageNote": x.usage_note} for x in links(db, r)],
        "addedBy": actor(db), "createdAt": iso(r.created_at), "updatedAt": iso(r.updated_at), "archivedAt": iso(r.archived_at)}

def add_attribute(db, r, data, request, origin="USER_PROVIDED"):
    a = ReferenceAttribute(reference_id=r.id, attribute_type=data.attributeType, value=data.value.model_dump(mode="json"),
        origin=origin, review_status="UNREVIEWED" if origin == "AI_INFERRED" else "CONFIRMED", created_by=DEMO_USER)
    db.add(a)
    r.updated_at = now()
    db.flush()
    audit(db, request, r.project_id, "REFERENCE_ATTRIBUTE_REVIEWED", "REFERENCE_ATTRIBUTE", a.id, "Reference attribute recorded.")
    return a

def add_link(db, r, data, request):
    if data.targetType == "DIRECTION_REVISION":
        owned(db, DirectionRevision, data.targetId, r.project_id)
    else:
        a = get_asset(db, data.targetId) if data.targetType == "ASSET" else get_asset(db, get_version(db, data.targetId).asset_id)
        if a.project_id != r.project_id:
            raise DomainError(422, "CROSS_PROJECT_REFERENCE", "The target belongs to another project.")
    relationship = (data.relationshipType or "INSPIRATION") if data.targetType == "ASSET" else ""
    existing = db.scalar(select(ReferenceLink).where(ReferenceLink.reference_id == r.id, ReferenceLink.target_type == data.targetType,
        ReferenceLink.target_id == str(data.targetId), ReferenceLink.relationship_type == relationship))
    if existing:
        return existing
    link = ReferenceLink(reference_id=r.id, target_type=data.targetType, target_id=str(data.targetId), relationship_type=relationship, usage_note=data.usageNote)
    db.add(link)
    r.updated_at = now()
    db.flush()
    audit(db, request, r.project_id, "REFERENCE_LINKED", "REFERENCE", r.id, "Reference linked.")
    return link

def asset_links(db, asset_id):
    rows = list(db.scalars(select(ReferenceLink).where(ReferenceLink.target_type == "ASSET", ReferenceLink.target_id == asset_id)))
    return [{"referenceId": x.reference_id, "relationshipType": x.relationship_type, "note": x.usage_note} for x in rows]
