import base64
import hashlib
import json
import re
from datetime import timezone
from uuid import uuid4
from sqlalchemy import select
from app.core.errors import DomainError, invalid
from app.core.models import User, Project, BriefRevision, Direction, DirectionRevision, Decision, Activity, IdempotencyRecord, now

DEMO_USER = "00000000-0000-4000-8000-000000000001"
DEMO_PROJECT = "00000000-0000-4000-8000-000000000010"

def actor(db):
    user = db.get(User, DEMO_USER)
    return {"id": user.id, "displayName": user.display_name}

def iso(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if value.tzinfo is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def get_project(db, identifier, lock=False):
    query = select(Project).where(Project.id == str(identifier), Project.owner_user_id == DEMO_USER)
    if lock:
        query = query.with_for_update()
    item = db.scalar(query)
    if item is None:
        raise DomainError(404, "NOT_FOUND", "Project not found.")
    return item

def owned(db, cls, identifier, project_id=None):
    item = db.get(cls, str(identifier))
    if item is None:
        raise DomainError(404, "NOT_FOUND", "Resource not found.")
    parent = db.get(Direction, item.direction_id) if cls is DirectionRevision else item
    get_project(db, parent.project_id)
    if project_id is not None and parent.project_id != project_id:
        raise DomainError(422, "CROSS_PROJECT_REFERENCE", "The referenced resource belongs to another project.")
    return item

def project_dto(db, p):
    return {"id": p.id, "name": p.name, "slug": p.slug, "description": p.description, "status": p.status,
        "owner": actor(db), "currentUserRole": "OWNER", "activeDirectionRevisionId": p.active_direction_revision_id,
        "createdAt": iso(p.created_at), "updatedAt": iso(p.updated_at), "archivedAt": iso(p.archived_at)}

def revision_dto(db, r):
    base = {"id": r.id, "revisionNumber": r.revision_number, "createdBy": actor(db), "createdAt": iso(r.created_at)}
    base["projectId" if isinstance(r, BriefRevision) else "directionId"] = r.project_id if isinstance(r, BriefRevision) else r.direction_id
    return {**base, **r.content}

def direction_dto(db, d):
    revisions = list(db.scalars(select(DirectionRevision).where(DirectionRevision.direction_id == d.id).order_by(DirectionRevision.revision_number.desc())))
    p = get_project(db, d.project_id)
    return {"id": d.id, "projectId": d.project_id, "name": d.name, "description": d.description, "status": d.status,
        "isActive": any(r.id == p.active_direction_revision_id for r in revisions),
        "latestRevision": revision_dto(db, revisions[0]), "revisionCount": len(revisions), "createdBy": actor(db),
        "createdAt": iso(d.created_at), "updatedAt": iso(d.updated_at), "archivedAt": iso(d.archived_at)}

def audit(db, request, project_id, event, entity, identifier, summary):
    db.add(Activity(project_id=project_id, actor_user_id=DEMO_USER, event_type=event,
        entity_type=entity, entity_id=identifier, summary=summary, request_id=getattr(request.state, "request_id", None)))

def check_timestamp(entity, expected):
    if iso(entity.updated_at) != iso(expected):
        raise DomainError(409, "CONFLICT", "This resource changed. Refresh before saving.")

def paginate(items, limit, cursor, scope, mapper):
    offset = 0
    if cursor:
        try:
            raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
            if raw["scope"] != scope or not isinstance(raw["offset"], int) or raw["offset"] < 0:
                raise ValueError()
            offset = raw["offset"]
        except Exception:
            invalid("Invalid pagination cursor.", "query.cursor")
    selected = items[offset:offset + limit]
    end = offset + len(selected)
    next_cursor = base64.urlsafe_b64encode(json.dumps({"scope": scope, "offset": end}).encode()).decode() if end < len(items) else None
    return {"data": [mapper(x) for x in selected], "page": {"limit": limit, "nextCursor": next_cursor}}

def create_project(db, data, request, fixed_id=None):
    slug = re.sub(r"[^a-z0-9]+", "-", data.name.lower()).strip("-") or "project"
    original = slug
    suffix = 1
    while db.scalar(select(Project.id).where(Project.owner_user_id == DEMO_USER, Project.slug == slug)):
        suffix += 1
        slug = original + "-" + str(suffix)
    p = Project(id=fixed_id or str(uuid4()), owner_user_id=DEMO_USER, name=data.name, slug=slug, description=data.description)
    db.add(p)
    db.flush()
    audit(db, request, p.id, "PROJECT_CREATED", "PROJECT", p.id, "Project created.")
    return p

def save_brief(db, p, data, request, fixed_id=None):
    revisions = list(db.scalars(select(BriefRevision).where(BriefRevision.project_id == p.id)))
    if revisions and not data.changeSummary:
        invalid("A change summary is required for a new revision.", "body.changeSummary")
    r = BriefRevision(id=fixed_id or str(uuid4()), project_id=p.id, revision_number=len(revisions) + 1,
        content=data.model_dump(mode="json"), created_by=DEMO_USER)
    db.add(r)
    p.updated_at = now()
    db.flush()
    audit(db, request, p.id, "BRIEF_REVISION_CREATED", "BRIEF_REVISION", r.id, "Brief revision created.")
    return r

def save_direction(db, p, data, request, fixed_id=None, fixed_revision_id=None):
    content = {}
    if data.cloneFromDirectionRevisionId:
        source = owned(db, DirectionRevision, data.cloneFromDirectionRevisionId, p.id)
        content = dict(source.content)
    if data.revision:
        content.update(data.revision.model_dump(mode="json", exclude_unset=bool(data.cloneFromDirectionRevisionId)))
    from app.modules.workspace.schemas import RevisionInput
    content = RevisionInput.model_validate(content).model_dump(mode="json")
    d = Direction(id=fixed_id or str(uuid4()), project_id=p.id, name=data.name, description=data.description, created_by=DEMO_USER)
    db.add(d)
    db.flush()
    r = DirectionRevision(id=fixed_revision_id or str(uuid4()), direction_id=d.id, revision_number=1, content=content, created_by=DEMO_USER)
    db.add(r)
    db.flush()
    audit(db, request, p.id, "DIRECTION_CREATED", "DIRECTION", d.id, "Direction created.")
    audit(db, request, p.id, "DIRECTION_REVISION_CREATED", "DIRECTION_REVISION", r.id, "Direction revision created.")
    return d

def activate(db, p, revision, reason, request):
    target = owned(db, DirectionRevision, revision, p.id)
    direction = db.get(Direction, target.direction_id)
    if direction.status == "ARCHIVED":
        raise DomainError(409, "INVALID_STATE_TRANSITION", "An archived direction cannot be activated.")
    previous = p.active_direction_revision_id
    if previous:
        old = db.get(DirectionRevision, previous)
        old_direction = db.get(Direction, old.direction_id)
        if old_direction.id != direction.id:
            old_direction.status = "SUPERSEDED"
            old_direction.updated_at = now()
    direction.status = "ACTIVE"
    direction.updated_at = now()
    p.active_direction_revision_id = target.id
    p.updated_at = now()
    decision = Decision(project_id=p.id, direction_revision_id=target.id, rationale=reason, created_by=DEMO_USER)
    db.add(decision)
    db.flush()
    audit(db, request, p.id, "ACTIVE_DIRECTION_CHANGED", "DIRECTION_REVISION", target.id, "Active creative direction changed.")
    return {"project": project_dto(db, p), "previousDirectionRevisionId": previous, "decisionId": decision.id}

def direction_diff(db, source, target):
    new = owned(db, DirectionRevision, target)
    old = owned(db, DirectionRevision, source, db.get(Direction, new.direction_id).project_id) if source else None
    categories = {"summary": "SUMMARY", "palette": "PALETTE", "lighting": "LIGHTING", "composition": "COMPOSITION",
        "materials": "MATERIAL", "mood": "MOOD", "typography": "TYPOGRAPHY", "stylePrompt": "STYLE_PROMPT",
        "requiredAttributes": "OTHER", "forbiddenAttributes": "OTHER"}
    changes = []
    for field, category in categories.items():
        before = old.content.get(field) if old else None
        after = new.content.get(field)
        if before != after:
            def display(value):
                if value is None:
                    return None
                return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
            changes.append({"category": category, "field": field, "from": display(before), "to": display(after),
                "kind": "ADDED" if before is None else "REMOVED" if after is None else "CHANGED"})
    return {"fromRevisionId": old.id if old else None, "toRevisionId": new.id,
        "summary": str(len(changes)) + " creative direction fields changed.", "changes": changes}
