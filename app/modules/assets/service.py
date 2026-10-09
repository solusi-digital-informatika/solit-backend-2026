import hashlib
import json
from sqlalchemy import select
from fastapi.responses import JSONResponse
from app.core.models import User, BriefRevision, DirectionRevision, Direction, IdempotencyRecord, now
from app.core.errors import DomainError, invalid
from app.modules.workspace.service import get_project, owned, actor, iso, audit, DEMO_USER
from app.modules.assets.models import Asset, AssetVersion, VersionDecision

def get_asset(db, identifier, lock=False):
    a = db.get(Asset, str(identifier))
    if not a:
        raise DomainError(404, "NOT_FOUND", "Asset not found.")
    get_project(db, a.project_id, lock=lock)
    if lock:
        db.refresh(a)
    return a

def get_version(db, identifier):
    v = db.get(AssetVersion, str(identifier))
    if not v:
        raise DomainError(404, "NOT_FOUND", "Asset version not found.")
    get_asset(db, v.asset_id)
    return v

def versions(db, a):
    return list(db.scalars(select(AssetVersion).where(AssetVersion.asset_id == a.id).order_by(AssetVersion.version_number.desc())))

def summary(v):
    from types import SimpleNamespace
    from app.modules.references.storage import file_url
    storage_id = v.content.get("thumbnailStorageObjectId") or v.content.get("storageObjectId")
    thumbnail = file_url(SimpleNamespace(id=storage_id)) if storage_id else v.content.get("externalFileUrl")
    return {"id": v.id, "assetId": v.asset_id, "versionNumber": v.version_number, "status": v.status,
        "thumbnailUrl": thumbnail, "createdAt": iso(v.created_at)}

def decision_dto(db, d):
    from app.modules.assessments.models import AssessmentDecision
    if isinstance(d, AssessmentDecision):
        from app.modules.assessments.service import decision_dto as assessment_decision_dto
        return assessment_decision_dto(db, d)
    return {"id": d.id, "projectId": d.project_id, "assessmentItemId": None,
        "assetId": d.asset_id, "assetVersionId": d.asset_version_id,
        "directionRevisionId": None, "collectionRevisionId": getattr(d, "collection_revision_id", None), "decisionType": d.decision_type,
        "selectedAction": None, "rationale": d.rationale, "previousRecommendation": None,
        "newRecommendation": None, "supersedesDecisionId": None, "supersededByDecisionId": None,
        "createdBy": actor(db), "createdAt": iso(d.created_at)}

def asset_dto(db, a, detail=False):
    rows = versions(db, a)
    owner = db.get(User, a.owner_user_id) if a.owner_user_id else None
    value = {"id": a.id, "projectId": a.project_id, "title": a.title, "description": a.description,
        "assetType": a.asset_type, "status": a.status, "tags": a.tags,
        "owner": {"id": owner.id, "displayName": owner.display_name} if owner else None,
        "versionCount": len(rows), "latestVersion": summary(rows[0]) if rows else None,
        "createdBy": actor(db), "createdAt": iso(a.created_at), "updatedAt": iso(a.updated_at), "archivedAt": iso(a.archived_at)}
    if detail:
        decisions = list(db.scalars(select(VersionDecision).where(VersionDecision.asset_id == a.id).order_by(VersionDecision.created_at.desc()).limit(10)))
        from app.modules.collections.models import PinDecision
        decisions.extend(db.scalars(select(PinDecision).where(PinDecision.asset_id == a.id).order_by(PinDecision.created_at.desc()).limit(10)))
        from app.modules.assessments.models import AssessmentDecision
        decisions.extend(db.scalars(select(AssessmentDecision).where(AssessmentDecision.asset_id == a.id).order_by(AssessmentDecision.created_at.desc()).limit(10)))
        decisions.sort(key=lambda d: (iso(d.created_at), d.id), reverse=True)
        from app.modules.references.service import asset_links
        value.update(versions=[summary(v) for v in rows], referenceLinks=asset_links(db, a.id), recentDecisions=[decision_dto(db, d) for d in decisions[:10]])
    return value

def version_dto(db, v):
    a = get_asset(db, v.asset_id)
    rows = versions(db, a)
    parent = db.get(AssetVersion, v.derived_from_version_id) if v.derived_from_version_id else None
    children = list(db.scalars(select(AssetVersion).where(AssetVersion.derived_from_version_id == v.id).order_by(AssetVersion.version_number)))
    from app.modules.collections.service import pinned_in
    from app.modules.references.storage import get_storage, file_url
    c = v.content
    storage = get_storage(db, c["storageObjectId"]) if c.get("storageObjectId") else None
    resolved_url = file_url(storage) if storage else c["externalFileUrl"]
    return {"id": v.id, "assetId": a.id, "assetTitle": a.title, "versionNumber": v.version_number,
        "derivedFromVersionId": v.derived_from_version_id, "directionRevisionId": v.direction_revision_id,
        "briefRevisionId": v.brief_revision_id, "fileUrl": resolved_url, "thumbnailUrl": summary(v)["thumbnailUrl"],
        "externalFileUrl": c["externalFileUrl"], "isMetadataOnly": not (storage or c["externalFileUrl"]),
        "mimeType": c["mimeType"], "width": c["width"], "height": c["height"], "contentChecksum": storage.checksum if storage else None,
        "prompt": c["prompt"], "negativePrompt": c["negativePrompt"], "providerName": c["providerName"],
        "modelName": c["modelName"], "modelVersion": c["modelVersion"], "generationSettings": c["generationSettings"],
        "recordedAttributes": c["recordedAttributes"], "revisionRationale": c["revisionRationale"],
        "status": v.status, "references": c["references"], "isLatest": rows[0].id == v.id, "pinnedIn": pinned_in(db, v.id),
        "lineage": {"derivedFrom": summary(parent) if parent else None, "derivedVersions": [summary(child) for child in children]},
        "createdBy": actor(db), "createdAt": iso(v.created_at)}

def owner_id(db, value):
    if value is not None:
        # Demo owner is the only current project member.
        if str(value) != DEMO_USER or not db.get(User, str(value)):
            raise DomainError(404, "NOT_FOUND", "Project member not found.")
        return str(value)
    return None

def create_version(db, a, data, request, fixed_id=None):
    p = get_project(db, a.project_id, lock=True)
    if data.derivedFromVersionId:
        parent = get_version(db, data.derivedFromVersionId)
        if parent.asset_id != a.id:
            if get_asset(db, parent.asset_id).project_id != a.project_id:
                raise DomainError(422, "CROSS_PROJECT_REFERENCE", "The parent version belongs to another project.")
            invalid("The parent version must belong to the same asset.", "body.derivedFromVersionId")
    direction = str(data.directionRevisionId) if data.directionRevisionId else (None if "directionRevisionId" in data.model_fields_set else p.active_direction_revision_id)
    brief = str(data.briefRevisionId) if data.briefRevisionId else None
    if "briefRevisionId" not in data.model_fields_set:
        brief = db.scalar(select(BriefRevision.id).where(BriefRevision.project_id == p.id).order_by(BriefRevision.revision_number.desc()))
    if direction:
        owned(db, DirectionRevision, direction, p.id)
    if brief:
        owned(db, BriefRevision, brief, p.id)
    from app.modules.references.storage import get_storage
    from app.modules.references.service import get_reference
    storage = get_storage(db, data.storageObjectId, p.id) if data.storageObjectId else None
    if data.thumbnailStorageObjectId:
        get_storage(db, data.thumbnailStorageObjectId, p.id)
    for reference in data.references:
        get_reference(db, reference.referenceId, p.id)
    content = data.model_dump(mode="json")
    if storage:
        content.update(mimeType=storage.mime_type, width=storage.width, height=storage.height)
    elif not data.externalFileUrl:
        content.update(mimeType=None, width=None, height=None)
    count = len(versions(db, a))
    kwargs = {"id": fixed_id} if fixed_id else {}
    v = AssetVersion(**kwargs, asset_id=a.id, version_number=count + 1,
        derived_from_version_id=str(data.derivedFromVersionId) if data.derivedFromVersionId else None,
        direction_revision_id=direction, brief_revision_id=brief, content=content, status=data.status, created_by=DEMO_USER)
    db.add(v)
    a.updated_at = now()
    db.flush()
    audit(db, request, a.project_id, "ASSET_VERSION_CREATED", "ASSET_VERSION", v.id, "Asset version created.")
    from app.modules.references.service import add_link
    from app.modules.references.schemas import LinkInput
    for reference in data.references:
        add_link(db, get_reference(db, reference.referenceId, p.id),
            LinkInput(targetType="ASSET_VERSION", targetId=v.id, usageNote=reference.usageNote), request)
    return v

def idempotent(db, key, route, data):
    if key is None:
        return None, None
    db.scalar(select(User).where(User.id == DEMO_USER).with_for_update())
    digest = hashlib.sha256(json.dumps(data.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(IdempotencyRecord).where(IdempotencyRecord.actor_id == DEMO_USER,
        IdempotencyRecord.route == route, IdempotencyRecord.key == key))
    if existing:
        if existing.request_hash != digest:
            raise DomainError(422, "IDEMPOTENCY_KEY_REUSED", "This idempotency key was used with different input.")
        return JSONResponse(existing.response_body, headers={"Idempotent-Replayed": "true"}), digest
    return None, digest

def remember(db, key, route, digest, body):
    if key:
        db.add(IdempotencyRecord(actor_id=DEMO_USER, route=route, key=key, request_hash=digest, response_body=body))
