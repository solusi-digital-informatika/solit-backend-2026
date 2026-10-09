from sqlalchemy import select
from app.core.errors import DomainError, invalid
from app.core.models import now
from app.modules.workspace.service import get_project, actor, iso, audit, DEMO_USER
from app.modules.assets.service import get_version, get_asset, versions, summary
from app.modules.collections.models import Collection, CollectionRevision, CollectionItem, Approval, PinDecision

def get_collection(db, identifier, lock=False):
    c = db.get(Collection, str(identifier))
    if c is None:
        raise DomainError(404, "NOT_FOUND", "Collection not found.")
    get_project(db, c.project_id, lock=lock)
    if lock:
        db.refresh(c)
    return c

def get_revision(db, identifier, lock=False):
    r = db.get(CollectionRevision, str(identifier))
    if r is None:
        raise DomainError(404, "NOT_FOUND", "Collection revision not found.")
    get_collection(db, r.collection_id, lock=lock)
    if lock:
        db.refresh(r)
    return r

def get_item(db, identifier):
    item = db.get(CollectionItem, str(identifier))
    if item is None:
        raise DomainError(404, "NOT_FOUND", "Collection item not found.")
    get_revision(db, item.collection_revision_id, lock=True)
    db.refresh(item)
    return item

def items(db, r):
    return list(db.scalars(select(CollectionItem).where(CollectionItem.collection_revision_id == r.id).order_by(CollectionItem.position)))

def revisions(db, c):
    return list(db.scalars(select(CollectionRevision).where(CollectionRevision.collection_id == c.id).order_by(CollectionRevision.revision_number.desc())))

def frozen(r):
    if r.status != "DRAFT":
        raise DomainError(409, "REVISION_FROZEN", "Only draft collection revisions can be edited.")

def ref(db, v):
    a = get_asset(db, v.asset_id)
    return {**summary(v), "assetTitle": a.title, "assetType": a.asset_type}

def item_dto(db, item):
    pinned = get_version(db, item.asset_version_id)
    latest = versions(db, get_asset(db, item.asset_id))[0]
    newer = latest.version_number - pinned.version_number
    return {"id": item.id, "collectionRevisionId": item.collection_revision_id, "position": item.position,
        "role": item.role, "note": item.note, "pinnedVersion": ref(db, pinned), "latestVersion": ref(db, latest),
        "isStale": newer > 0, "newerVersionCount": newer}

def revision_dto(db, r, detail=False):
    rows = [item_dto(db, i) for i in items(db, r)]
    dto = {"id": r.id, "collectionId": r.collection_id, "revisionNumber": r.revision_number,
        "status": r.status, "changeSummary": r.change_summary, "itemCount": len(rows),
        "staleItemCount": sum(i["isStale"] for i in rows), "createdBy": actor(db), "createdAt": iso(r.created_at),
        "submittedAt": iso(r.submitted_at), "approvedAt": iso(r.approved_at)}
    if detail:
        approvals = list(db.scalars(select(Approval).where(Approval.collection_revision_id == r.id).order_by(Approval.reviewed_at.desc())))
        dto.update(collectionName=get_collection(db, r.collection_id).name, items=rows, approvals=[
            {"id": a.id, "collectionRevisionId": r.id, "decision": a.decision, "comment": a.comment,
             "reviewedBy": actor(db), "reviewedAt": iso(a.reviewed_at)} for a in approvals])
    return dto

def collection_dto(db, c, detail=False):
    rows = revisions(db, c)
    approved = next((r for r in rows if r.status == "APPROVED"), None)
    dto = {"id": c.id, "projectId": c.project_id, "name": c.name, "description": c.description,
        "collectionType": c.collection_type, "status": c.status, "latestRevision": revision_dto(db, rows[0]),
        "approvedRevisionId": approved.id if approved else None, "createdBy": actor(db), "createdAt": iso(c.created_at),
        "updatedAt": iso(c.updated_at), "archivedAt": iso(c.archived_at)}
    if detail:
        dto["revisions"] = [revision_dto(db, r) for r in rows]
    return dto

def checked_version(db, c, identifier):
    v = get_version(db, identifier)
    if get_asset(db, v.asset_id).project_id != c.project_id:
        raise DomainError(422, "CROSS_PROJECT_REFERENCE", "This version belongs to another project.")
    return v

def arrange(db, rows):
    # Two phases avoid transient unique(position) conflicts during reordering.
    for index, item in enumerate(rows):
        item.position = -index - 1
    db.flush()
    for index, item in enumerate(rows):
        item.position = index + 1
    db.flush()

def pin_decision(db, c, r, v, request, kind, rationale):
    db.add(PinDecision(project_id=c.project_id, collection_revision_id=r.id, asset_id=v.asset_id,
        asset_version_id=v.id, decision_type=kind, rationale=rationale, created_by=DEMO_USER))
    audit(db, request, c.project_id, "COLLECTION_ITEM_REPLACED" if kind == "REPLACE_PINNED_VERSION" else "COLLECTION_ITEM_PINNED",
        "COLLECTION_REVISION", r.id, rationale)

def add_item(db, c, r, data, request):
    frozen(r)
    v = checked_version(db, c, data.assetVersionId)
    rows = items(db, r)
    if any(item.asset_id == v.asset_id for item in rows):
        raise DomainError(409, "ASSET_ALREADY_PINNED", "This asset is already pinned in the revision.")
    position = getattr(data, "position", None) or len(rows) + 1
    if position > len(rows) + 1:
        invalid("Position must be within the collection.", "body.position")
    item = CollectionItem(collection_revision_id=r.id, asset_id=v.asset_id, asset_version_id=v.id,
        position=0, role=data.role, note=data.note)
    db.add(item)
    db.flush()
    rows.insert(position - 1, item)
    arrange(db, rows)
    c.updated_at = now()
    pin_decision(db, c, r, v, request, "PIN_TO_COLLECTION", "Pinned asset version to collection.")
    return item

def pinned_in(db, version_id):
    entries = list(db.scalars(select(CollectionItem).where(CollectionItem.asset_version_id == version_id)))
    result = []
    for item in entries:
        r = db.get(CollectionRevision, item.collection_revision_id)
        c = db.get(Collection, r.collection_id)
        result.append({"collectionId": c.id, "collectionName": c.name, "collectionRevisionId": r.id,
            "revisionNumber": r.revision_number, "revisionStatus": r.status})
    return result
