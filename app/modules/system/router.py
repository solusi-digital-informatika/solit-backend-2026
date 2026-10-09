from uuid import UUID
import time
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.demo import reset_demo
from app.modules.workspace.service import get_project, project_dto, audit
from app.modules.workspace.schemas import ProjectResponse
from app.modules.system.schemas import *
from app.modules.system.service import summary, ai_settings, project_export

router = APIRouter(prefix="/api/v1")

@router.get("/projects/{project_id}/summary", response_model=SummaryResponse)
def project_summary(project_id: UUID, db: Session = Depends(get_db)):
    return {"data": summary(db, get_project(db, project_id))}

@router.get("/settings/ai", response_model=AiSettingsResponse)
def settings():
    return {"data": ai_settings()}

@router.post("/settings/ai/test", response_model=AiTestResponse)
def test_ai():
    s = get_settings()
    if s.ai_mode == "mock":
        return {"data": {"ok": True, "latencyMs": 0, "errorCode": None, "message": "Simulated provider is available; no network call was made."}}
    from app.core.ai_router import complete
    started = time.perf_counter()
    complete([{"role": "user", "content": "Reply with OK."}], structured=False, max_tokens=8)
    return {"data": {"ok": True, "latencyMs": round((time.perf_counter() - started) * 1000), "errorCode": None,
        "message": "The configured AI router is reachable."}}

@router.post("/projects/{project_id}/exports", status_code=201, response_model=ExportResponse)
def export(project_id: UUID, data: ExportInput, request: Request, db: Session = Depends(get_db)):
    p = get_project(db, project_id, lock=True)
    result = project_export(db, p, data.format)
    audit(db, request, p.id, "EXPORT_CREATED", "EXPORT", result["id"], "Project export created.")
    db.commit()
    return {"data": result}

@router.post("/demo/reset", response_model=ProjectResponse)
def demo_reset(data: ResetInput, request: Request, db: Session = Depends(get_db)):
    if not get_settings().demo_mode:
        raise DomainError(403, "FORBIDDEN", "Demo reset is disabled.")
    p = reset_demo(db, request)
    db.commit()
    return {"data": project_dto(db, p)}
