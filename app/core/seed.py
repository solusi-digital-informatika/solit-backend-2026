from types import SimpleNamespace
from sqlalchemy import select
from app.core.models import User, Project, DirectionRevision
from app.modules.workspace.schemas import ProjectInput, BriefInput, DirectionInput
from app.modules.workspace.service import DEMO_USER, DEMO_PROJECT, create_project, save_brief, save_direction, activate

def seed_workspace(db, request=None, fixed=True, name=None, description=None, complete=True, rebuild_workspace=False):
    request = request or SimpleNamespace(state=SimpleNamespace(request_id=None))
    if db.get(User, DEMO_USER) is None:
        db.add(User(id=DEMO_USER, display_name="Demo Owner"))
        db.flush()
    existing = db.get(Project, DEMO_PROJECT) if fixed else None
    if existing and not rebuild_workspace:
        if complete:
            from app.core.demo import seed_demo
            seed_demo(db, existing, request, fixed=fixed)
        return existing
    def identifier(number):
        return "00000000-0000-4000-8000-" + str(number).zfill(12) if fixed else None
    p = existing or create_project(db, ProjectInput(name=name or "Solara — Product Visual Campaign", description=description, template="BLANK"), request, identifier(10))
    save_brief(db, p, BriefInput(title="Solara Product Launch", objective="Create a coherent product visual campaign.",
        targetAudience="Design-conscious consumers", deliverables=["Hero image", "Product close-up", "Background", "Texture"],
        acceptanceCriteria=["Preserve approved production history."]), request, identifier(20))
    a = save_direction(db, p, DirectionInput(name="Cold Industrial", revision={
        "summary": "Precise industrial product visuals.",
        "palette": [{"name": x} for x in ["steel blue", "graphite", "cool white"]],
        "lighting": {"quality": "hard directional high-contrast light", "temperature": "cool"},
        "materials": ["metal", "glass", "polished surfaces"], "mood": ["precise", "technical", "premium"],
    }), request, identifier(30), identifier(31))
    save_direction(db, p, DirectionInput(name="Warm Organic", revision={
        "summary": "Approachable organic product visuals.",
        "palette": [{"name": x} for x in ["amber", "cream", "warm brown", "muted green"]],
        "lighting": {"quality": "soft natural light", "temperature": "warm"},
        "materials": ["wood", "paper", "stone", "natural textures"], "mood": ["approachable", "calm", "crafted"],
    }), request, identifier(40), identifier(41))
    activate(db, p, identifier(31) if fixed else db.scalar(select(DirectionRevision.id).where(DirectionRevision.direction_id == a.id)), "Initial demo direction.", request)
    if complete:
        from app.core.demo import seed_demo
        seed_demo(db, p, request, fixed=fixed)
    return p
