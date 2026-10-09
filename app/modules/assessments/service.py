from sqlalchemy import select
from app.core.models import BriefRevision, DirectionRevision, now
from app.core.errors import DomainError, invalid
from app.core.config import get_settings
from app.core.ai_router import require_configured, image_data_url, PROMPT_VERSION
from app.modules.workspace.service import get_project, owned, actor, iso, audit, direction_diff, DEMO_USER
from app.modules.assets.models import Asset
from app.modules.assets.service import get_asset, get_version, versions
from app.modules.collections.models import CollectionItem, CollectionRevision
from app.modules.collections.service import ref
from app.modules.assessments.models import Assessment, AssessmentItem, AssessmentDecision
from app.modules.assessments.schemas import ProviderItem
from app.modules.assessments.rules import RECOMMENDATIONS
from app.modules.assessments import provider

def get_assessment(db, identifier, lock=False):
    a = db.get(Assessment, str(identifier))
    if not a:
        raise DomainError(404, "NOT_FOUND", "Assessment not found.")
    get_project(db, a.project_id, lock=lock)
    if lock:
        db.refresh(a)
    return a

def assessment_items(db, a):
    return list(db.scalars(select(AssessmentItem).where(AssessmentItem.assessment_id == a.id).order_by(AssessmentItem.priority)))

def latest_decision(db, item):
    return db.scalar(select(AssessmentDecision).where(AssessmentDecision.assessment_item_id == item.id).order_by(AssessmentDecision.created_at.desc(), AssessmentDecision.id.desc()))

def decision_dto(db, d):
    successor = db.scalar(select(AssessmentDecision.id).where(AssessmentDecision.supersedes_decision_id == d.id))
    return {"id": d.id, "projectId": d.project_id, "assessmentItemId": d.assessment_item_id, "assetId": d.asset_id,
        "assetVersionId": d.asset_version_id, "directionRevisionId": None, "collectionRevisionId": None,
        "decisionType": d.decision_type, "selectedAction": d.selected_action, "rationale": d.rationale,
        "previousRecommendation": d.previous_recommendation, "newRecommendation": d.new_recommendation,
        "supersedesDecisionId": d.supersedes_decision_id, "supersededByDecisionId": successor,
        "createdBy": actor(db), "createdAt": iso(d.created_at)}

def item_dto(db, item):
    current = latest_decision(db, item)
    content = {k: v for k, v in item.content.items() if k not in ("assetVersionId", "recommendation")}
    return {"id": item.id, "assessmentId": item.assessment_id, "assetVersion": ref(db, get_version(db, item.asset_version_id)),
        "recommendation": item.recommendation, **content, "priority": item.priority,
        "resolutionStatus": item.resolution_status, "effectiveRecommendation": current.new_recommendation if item.resolution_status == "OVERRIDDEN" and current else item.recommendation,
        "latestDecision": decision_dto(db, current) if current else None, "createdAt": iso(item.created_at), "updatedAt": iso(item.updated_at)}

def assessment_dto(db, a, detail=True, recommendation=None, resolution=None):
    rows = assessment_items(db, a)
    counts = {r: sum(i.recommendation == r for i in rows) for r in RECOMMENDATIONS}
    selected = [i for i in rows if (not recommendation or i.recommendation in recommendation) and (not resolution or i.resolution_status in resolution)]
    meta = a.result_metadata
    return {"id": a.id, "projectId": a.project_id, "oldDirectionRevisionId": a.old_direction_revision_id,
        "newDirectionRevisionId": a.new_direction_revision_id, "briefRevisionId": a.brief_revision_id,
        "mode": a.mode, "status": a.status, "providerName": meta.get("providerName", "mock" if meta["isSimulated"] else None),
        "modelName": meta.get("modelName", "mock" if meta["isSimulated"] else None), "promptTemplateVersion": meta.get("promptTemplateVersion"),
        "outputSchemaVersion": "1.0.0", "rulesVersion": "rules-v1", "isSimulated": meta["isSimulated"], "imagesSent": meta.get("imagesSent", 0),
        "directionDiff": meta["directionDiff"], "summary": meta.get("summary"), "errorCode": meta.get("errorCode"),
        "errorSummary": meta.get("errorSummary"), "counts": {"total": len(a.input_snapshot["versions"]), "byRecommendation": counts,
        "unresolved": sum(i.resolution_status in ("UNRESOLVED", "DEFERRED") for i in rows)},
        "items": [item_dto(db, i) for i in selected] if detail and a.status == "COMPLETED" else None,
        "startedBy": actor(db), "startedAt": iso(a.started_at), "completedAt": iso(a.completed_at)}

def snapshot(db, p, data):
    old_id = str(data.oldDirectionRevisionId) if data.oldDirectionRevisionId else p.active_direction_revision_id
    target = owned(db, DirectionRevision, data.newDirectionRevisionId, p.id)
    if old_id == target.id:
        invalid("Old and new direction revisions must differ.", "body.newDirectionRevisionId")
    if old_id:
        owned(db, DirectionRevision, old_id, p.id)
    brief_id = str(data.briefRevisionId) if data.briefRevisionId else db.scalar(select(BriefRevision.id).where(BriefRevision.project_id == p.id).order_by(BriefRevision.revision_number.desc()))
    brief = owned(db, BriefRevision, brief_id, p.id) if brief_id else None
    if data.assetVersionIds is None:
        selected = []
        for asset in db.scalars(select(Asset).where(Asset.project_id == p.id, Asset.status != "ARCHIVED")):
            rows = versions(db, asset)
            if rows:
                selected.append(rows[0])
    else:
        selected = [get_version(db, identifier) for identifier in data.assetVersionIds]
        if any(get_asset(db, v.asset_id).project_id != p.id for v in selected):
            raise DomainError(422, "CROSS_PROJECT_REFERENCE", "An asset version belongs to another project.")
    settings = get_settings()
    if data.mode != "RULES_ONLY":
        if settings.ai_mode != "mock":
            require_configured()
        if len(selected) > settings.ai_max_assets_per_assessment:
            raise DomainError(422, "ASSESSMENT_SCOPE_TOO_LARGE", "The assessment exceeds the configured asset limit.")
    inputs = []
    for v in selected:
        pinned = db.scalar(select(CollectionItem.id).join(CollectionRevision, CollectionItem.collection_revision_id == CollectionRevision.id).where(
            CollectionItem.asset_version_id == v.id, CollectionRevision.status == "APPROVED")) is not None
        inputs.append({"id": v.id, "title": get_asset(db, v.asset_id).title, "recordedAttributes": v.content["recordedAttributes"], "pinned": pinned})
    images = []
    if data.mode != "RULES_ONLY" and settings.ai_mode != "mock" and data.includeImages and settings.ai_image_analysis_enabled:
        from app.modules.references.storage import get_storage, file_path
        from app.core.demo import demo_dir
        from urllib.parse import urlsplit
        for v in selected:
            path = None
            if v.content.get("storageObjectId"):
                path = file_path(get_storage(db, v.content["storageObjectId"], p.id))
            elif (v.content.get("externalFileUrl") or "").startswith("/demo-assets/"):
                demo_root = demo_dir().resolve()
                candidate = (demo_root / urlsplit(v.content["externalFileUrl"]).path.removeprefix("/demo-assets/")).resolve()
                if candidate.is_relative_to(demo_root):
                    path = candidate
            if path and path.is_file():
                images.append({"assetVersionId": v.id, "dataUrl": image_data_url(path)})
    old = db.get(DirectionRevision, old_id) if old_id else None
    return {"oldDirectionRevisionId": old_id, "newDirectionRevisionId": target.id, "briefRevisionId": brief_id,
        "oldDirection": old.content if old else None,
        "newDirection": target.content, "brief": brief.content if brief else None, "versions": inputs, "images": images}

def validate_output(raw, data):
    parsed = [ProviderItem.model_validate(item).model_dump(mode="json") for item in raw]
    expected = {v["id"] for v in data["versions"]}
    ids = [i["assetVersionId"] for i in parsed]
    if len(ids) != len(expected) or set(ids) != expected:
        raise ValueError("Provider scope mismatch")
    evidence_ids = expected | {x for x in (data["oldDirectionRevisionId"], data["newDirectionRevisionId"], data["briefRevisionId"]) if x}
    for item in parsed:
        for entry in item["supportingEvidence"] + item["conflictingEvidence"]:
            image_ids = {image["assetVersionId"] for image in data.get("images", [])}
            if entry["source"] == "IMAGE_OBSERVATION" and (entry["sourceId"] not in image_ids or entry["sourceId"] != item["assetVersionId"]):
                raise ValueError("Image evidence without a matching input image")
            if entry["sourceId"] is not None and entry["sourceId"] not in evidence_ids:
                raise ValueError("Invalid provider evidence")
        if not any(e["sourceId"] in evidence_ids for e in item["supportingEvidence"] + item["conflictingEvidence"]):
            raise ValueError("Missing traceable evidence")
    return parsed

def run(db, a, request):
    a.status = "RUNNING"
    settings = get_settings()
    simulated = a.mode != "RULES_ONLY" and settings.ai_mode == "mock"
    real = a.mode != "RULES_ONLY" and not simulated
    a.result_metadata = {**a.result_metadata, "isSimulated": simulated,
        "providerName": settings.ai_provider_name if real else "mock" if simulated else None,
        "modelName": (settings.ai_model or settings.openai_model) if real else "mock" if simulated else None,
        "promptTemplateVersion": PROMPT_VERSION if real else None,
        "imagesSent": len(a.input_snapshot.get("images", [])) if real else 0}
    try:
        if real:
            require_configured()
        parsed = validate_output(provider.analyze(a.input_snapshot, a.mode), a.input_snapshot)
    except Exception as exc:
        a.status, a.completed_at = "FAILED", now()
        code = exc.code if isinstance(exc, DomainError) else "PROVIDER_ERROR"
        if code in ("AI_NOT_CONFIGURED", "ASSESSMENT_SCOPE_TOO_LARGE"):
            a.result_metadata = {**a.result_metadata, "imagesSent": 0}
        a.result_metadata = {**a.result_metadata, "summary": None, "errorCode": code, "errorSummary": "Analysis failed or returned invalid output. Check the AI settings, retry or run rules-only."}
        audit(db, request, a.project_id, "ASSESSMENT_FAILED", "IMPACT_ASSESSMENT", a.id, "Impact assessment failed.")
        return
    for index, result in enumerate(parsed):
        db.add(AssessmentItem(assessment_id=a.id, asset_version_id=result["assetVersionId"],
            recommendation=result["recommendation"], content=result, priority=index + 1))
    a.status, a.completed_at = "COMPLETED", now()
    a.result_metadata = {**a.result_metadata, "summary": str(len(parsed)) + (" asset versions assessed using recorded metadata." if not real else " asset versions assessed by the configured AI router."),
        "errorCode": None, "errorSummary": None}
    db.flush()
    audit(db, request, a.project_id, "ASSESSMENT_COMPLETED", "IMPACT_ASSESSMENT", a.id, "Impact assessment completed.")

def resolve(db, a, identifier, data, request):
    if a.status != "COMPLETED":
        raise DomainError(409, "INVALID_STATE_TRANSITION", "Only a completed assessment can be resolved.")
    item = db.get(AssessmentItem, str(identifier))
    if not item or item.assessment_id != a.id:
        raise DomainError(404, "NOT_FOUND", "Assessment item not found.")
    db.refresh(item)
    if data.resolution == "OVERRIDE" and data.overrideRecommendation == item.recommendation:
        invalid("An override must differ from the original recommendation.", "body.overrideRecommendation")
    previous = latest_decision(db, item)
    mapping = {"ACCEPT": ("ACCEPT_RECOMMENDATION", "ACCEPTED"), "OVERRIDE": ("OVERRIDE_RECOMMENDATION", "OVERRIDDEN"),
        "DEFER": ("DEFER_RECOMMENDATION", "DEFERRED"), "DISMISS": ("DISMISS_RECOMMENDATION", "DISMISSED")}
    kind, status = mapping[data.resolution]
    new = data.overrideRecommendation if data.resolution == "OVERRIDE" else item.recommendation
    d = AssessmentDecision(project_id=a.project_id, assessment_item_id=item.id, asset_id=get_version(db, item.asset_version_id).asset_id,
        asset_version_id=item.asset_version_id, decision_type=kind, selected_action=data.selectedAction,
        rationale=data.rationale or ("Accepted without comment" if data.resolution == "ACCEPT" else "Deferred without comment"),
        previous_recommendation=previous.new_recommendation if previous and item.resolution_status == "OVERRIDDEN" else item.recommendation,
        new_recommendation=new, supersedes_decision_id=previous.id if previous else None, created_by=DEMO_USER)
    db.add(d)
    item.resolution_status, item.updated_at = status, now()
    db.flush()
    audit(db, request, a.project_id, "RECOMMENDATION_RESOLVED", "IMPACT_ASSESSMENT_ITEM", item.id, "Assessment recommendation resolved.")
    return {"decision": decision_dto(db, d), "item": item_dto(db, item)}
