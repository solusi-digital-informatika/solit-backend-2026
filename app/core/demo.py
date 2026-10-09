from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4
from PIL import Image, ImageDraw
from sqlalchemy import select, delete
from app.core.models import User, Project, BriefRevision, Direction, DirectionRevision, Decision, Activity, IdempotencyRecord, now
from app.modules.workspace.service import DEMO_USER, DEMO_PROJECT, audit
from app.modules.assets.models import Asset, AssetVersion, VersionDecision
from app.modules.assets.schemas import VersionInput
from app.modules.assets.service import create_version
from app.modules.references.models import Reference, ReferenceAttribute, ReferenceLink
from app.modules.references.schemas import AttributeInput, LinkInput
from app.modules.references.service import add_attribute, add_link
from app.modules.collections.models import Collection, CollectionRevision, CollectionItem, Approval, PinDecision
from app.modules.assessments.models import Assessment, AssessmentItem, AssessmentDecision
from app.modules.system.models import ExportRecord

DEMO_FILES = ["hero-v1.jpg", "hero-v2.jpg", "closeup-v1.jpg", "factory-bg-v1.jpg", "metal-texture-v1.jpg", "ref-steel.jpg", "ref-timber.jpg"]

def demo_dir():
    return Path(__file__).resolve().parent.parent / "demo-assets"

def ensure_demo_files():
    root = demo_dir()
    root.mkdir(parents=True, exist_ok=True)
    palettes = [("#354958", "#b0bec5"), ("#e7dbc0", "#617850"), ("#54636d", "#d9c6a5"),
        ("#35444e", "#8c999e"), ("#455764", "#b4bec1"), ("#4d606c", "#b5c1c6"), ("#bea582", "#e8d9b6")]
    for filename, colors in zip(DEMO_FILES, palettes):
        path = root / filename
        if path.exists():
            continue
        image = Image.new("RGB", (800, 600), colors[0])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((260, 140, 540, 450), radius=24, fill=colors[1])
        draw.text((24, 24), "BRANCHFRAME / SYNTHETIC DEMO", fill="white")
        draw.text((24, 550), filename, fill="white")
        image.save(path, "JPEG", quality=85)

def seed_demo(db, p, request=None, fixed=True):
    request = request or SimpleNamespace(state=SimpleNamespace(request_id=None))
    def identifier(number):
        return "00000000-0000-4000-8000-" + str(number).zfill(12) if fixed else str(uuid4())
    if db.scalar(select(Asset.id).where(Asset.project_id == p.id)):
        return p  # Restart must preserve edited direction names and versions.
    directions = list(db.scalars(select(Direction).where(Direction.project_id == p.id)))
    a = db.get(Direction, identifier(30)) if fixed else next((d for d in directions if d.name == "Cold Industrial"), None)
    b = db.get(Direction, identifier(40)) if fixed else next((d for d in directions if d.name == "Warm Organic"), None)
    if a is None or b is None:
        return p  # Existing customized workspace is not overwritten during startup.
    ar = db.scalar(select(DirectionRevision).where(DirectionRevision.direction_id == a.id).order_by(DirectionRevision.revision_number))
    br = db.scalar(select(DirectionRevision).where(DirectionRevision.direction_id == b.id).order_by(DirectionRevision.revision_number))
    if db.scalar(select(Asset.id).where(Asset.project_id == p.id)):
        return p  # Never rewrite an existing demo's versions on restart.
    definitions = [
        (50, "Hero Product Reveal", "HERO_IMAGE", "APPROVED", [
            (60, "hero-v1.jpg", "SUPERSEDED", {"palette": ["graphite"], "lighting": ["hard"], "materials": ["metal"], "mood": ["technical"], "composition": ["hero product"]}),
            (61, "hero-v2.jpg", "APPROVED", {"palette": ["cream"], "lighting": ["soft natural light"], "materials": ["wood"], "mood": ["calm"], "composition": ["hero product"]})]),
        (51, "Product Close-up", "PRODUCT_IMAGE", "APPROVED", [
            (62, "closeup-v1.jpg", "APPROVED", {"palette": ["graphite"], "lighting": ["hard"], "materials": ["wood"], "mood": ["calm"], "composition": ["product close-up"]})]),
        (52, "Factory Background", "BACKGROUND", "APPROVED", [
            (63, "factory-bg-v1.jpg", "APPROVED", {"palette": ["graphite"], "materials": ["metal"], "composition": ["factory background"]})]),
        (53, "Metal Texture Detail", "TEXTURE", "DRAFT", [
            (64, "metal-texture-v1.jpg", "CANDIDATE", {"palette": ["graphite"], "lighting": ["hard"], "materials": ["metal"], "mood": ["technical"], "composition": ["macro texture"]})]),
    ]
    saved = {}
    for number, title, kind, status, specs in definitions:
        asset = Asset(id=identifier(number), project_id=p.id, title=title, description="Synthetic demo scenario.",
            asset_type=kind, status=status, tags=["demo"], owner_user_id=DEMO_USER, created_by=DEMO_USER)
        db.add(asset)
        db.flush()
        audit(db, request, p.id, "ASSET_CREATED", "ASSET", asset.id, "Demo asset registered.")
        parent = None
        for version_number, filename, version_status, attributes in specs:
            v = create_version(db, asset, VersionInput(externalFileUrl="/demo-assets/" + filename,
                derivedFromVersionId=parent.id if parent else None, directionRevisionId=ar.id,
                recordedAttributes=attributes, revisionRationale="Synthetic demo version",
                mimeType="image/jpeg", width=800, height=600), request, fixed_id=identifier(version_number))
            v.status = version_status
            if version_status == "APPROVED":
                db.add(VersionDecision(project_id=p.id, asset_id=asset.id, asset_version_id=v.id,
                    decision_type="APPROVE_VERSION", rationale="Approved demo baseline.", created_by=DEMO_USER))
            saved[version_number] = v
            parent = v
    c = Collection(id=identifier(70), project_id=p.id, name="Solara Launch Hero Set", description="Approved demo baseline.",
        collection_type="DELIVERABLE", status="APPROVED", created_by=DEMO_USER)
    db.add(c)
    db.flush()
    r = CollectionRevision(id=identifier(71), collection_id=c.id, revision_number=1, status="APPROVED",
        change_summary=None, created_by=DEMO_USER, submitted_at=now(), approved_at=now())
    db.add(r)
    db.flush()
    for position, number in enumerate([61, 62, 63], 1):
        v = saved[number]
        db.add(CollectionItem(collection_revision_id=r.id, asset_id=v.asset_id, asset_version_id=v.id, position=position, role=None, note=None))
        db.add(PinDecision(project_id=p.id, collection_revision_id=r.id, asset_id=v.asset_id, asset_version_id=v.id,
            decision_type="PIN_TO_COLLECTION", rationale="Pinned approved demo baseline.", created_by=DEMO_USER))
    db.add(Approval(collection_revision_id=r.id, decision="APPROVED", comment="Demo baseline.", reviewed_by=DEMO_USER))
    audit(db, request, p.id, "COLLECTION_CREATED", "COLLECTION", c.id, "Demo collection created.")
    audit(db, request, p.id, "COLLECTION_APPROVED", "COLLECTION_REVISION", r.id, "Demo collection approved.")
    for number, title, filename, revision, category, text in [
        (80, "Brushed steel macro", "ref-steel.jpg", ar, "MATERIAL", "metal"),
        (81, "Warm timber studio", "ref-timber.jpg", br, "MATERIAL", "wood")]:
        reference = Reference(id=identifier(number), project_id=p.id, title=title, description="Synthetic visual placeholder.",
            source_type="DEMO_ASSET", source_url="/demo-assets/" + filename, storage_object_id=None,
            usage_rights_note="Locally generated synthetic placeholder for demonstration.", tags=["demo"], added_by=DEMO_USER)
        db.add(reference)
        db.flush()
        add_attribute(db, reference, AttributeInput(attributeType=category, value={"text": text}), request)
        add_link(db, reference, LinkInput(targetType="DIRECTION_REVISION", targetId=revision.id), request)
        audit(db, request, p.id, "REFERENCE_ADDED", "REFERENCE", reference.id, "Demo reference added.")
    db.flush()
    return p

def reset_demo(db, request):
    # The permanent demo project is the reset boundary. Other projects, users and files remain untouched.
    # Match actor-before-project lock order used by idempotent mutations.
    db.scalar(select(User).where(User.id == DEMO_USER).with_for_update())
    p = db.get(Project, DEMO_PROJECT)
    if p:
        from app.modules.workspace.service import get_project
        get_project(db, DEMO_PROJECT, lock=True)
        asset_ids = list(db.scalars(select(Asset.id).where(Asset.project_id == DEMO_PROJECT)))
        version_ids = list(db.scalars(select(AssetVersion.id).where(AssetVersion.asset_id.in_(asset_ids))))
        direction_ids = list(db.scalars(select(Direction.id).where(Direction.project_id == DEMO_PROJECT)))
        direction_revision_ids = list(db.scalars(select(DirectionRevision.id).where(DirectionRevision.direction_id.in_(direction_ids))))
        brief_ids = list(db.scalars(select(BriefRevision.id).where(BriefRevision.project_id == DEMO_PROJECT)))
        collection_ids = list(db.scalars(select(Collection.id).where(Collection.project_id == DEMO_PROJECT)))
        revision_ids = list(db.scalars(select(CollectionRevision.id).where(CollectionRevision.collection_id.in_(collection_ids))))
        reference_ids = list(db.scalars(select(Reference.id).where(Reference.project_id == DEMO_PROJECT)))
        assessment_ids = list(db.scalars(select(Assessment.id).where(Assessment.project_id == DEMO_PROJECT)))
        all_ids = set([DEMO_PROJECT, *asset_ids, *version_ids, *direction_ids, *direction_revision_ids, *brief_ids, *collection_ids, *revision_ids, *reference_ids, *assessment_ids])
        for record in db.scalars(select(IdempotencyRecord)):
            data = record.response_body.get("data", {})
            if data.get("id") in all_ids or any(identifier in record.route.split("/") for identifier in all_ids):
                db.delete(record)
        p.active_direction_revision_id = None
        db.flush()
        for cls, condition in [
            (AssessmentDecision, AssessmentDecision.project_id == DEMO_PROJECT),
            (AssessmentItem, AssessmentItem.assessment_id.in_(assessment_ids)),
            (Assessment, Assessment.project_id == DEMO_PROJECT),
            (PinDecision, PinDecision.project_id == DEMO_PROJECT),
            (Approval, Approval.collection_revision_id.in_(revision_ids)),
            (CollectionItem, CollectionItem.collection_revision_id.in_(revision_ids)),
            (CollectionRevision, CollectionRevision.collection_id.in_(collection_ids)),
            (Collection, Collection.project_id == DEMO_PROJECT),
            (ReferenceLink, ReferenceLink.reference_id.in_(reference_ids)),
            (ReferenceAttribute, ReferenceAttribute.reference_id.in_(reference_ids)),
            (Reference, Reference.project_id == DEMO_PROJECT),
            (VersionDecision, VersionDecision.project_id == DEMO_PROJECT),
            (AssetVersion, AssetVersion.asset_id.in_(asset_ids)),
            (Asset, Asset.project_id == DEMO_PROJECT),
            (Decision, Decision.project_id == DEMO_PROJECT),
            (DirectionRevision, DirectionRevision.direction_id.in_(direction_ids)),
            (Direction, Direction.project_id == DEMO_PROJECT),
            (BriefRevision, BriefRevision.project_id == DEMO_PROJECT),
            (Activity, Activity.project_id == DEMO_PROJECT),
            (ExportRecord, ExportRecord.project_id == DEMO_PROJECT),
        ]:
            db.execute(delete(cls).where(condition), execution_options={"synchronize_session": False})
        # Keep project identity so uploaded file FKs remain valid and uploaded files aren't destroyed.
        p.name, p.slug, p.description, p.status = "Solara — Product Visual Campaign", "solara-product-visual-campaign", None, "ACTIVE"
        p.archived_at, p.updated_at = None, now()
        db.flush()
        db.expunge_all()
        from app.core.seed import seed_workspace
        p = seed_workspace(db, request, fixed=True, rebuild_workspace=True)
    else:
        from app.core.seed import seed_workspace
        p = seed_workspace(db, request, fixed=True)
    audit(db, request, p.id, "DEMO_RESET", "PROJECT", p.id, "Demo reset to the baseline.")
    return p
