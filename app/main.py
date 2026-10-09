from contextlib import asynccontextmanager
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette.exceptions import HTTPException
from app.core.config import get_settings
from app.core.database import engine, SessionLocal
from app.core.migrations import migrate
from app.core.seed import seed_workspace
from app.core.demo import ensure_demo_files, demo_dir
from fastapi.staticfiles import StaticFiles
from app.core.errors import DomainError
from app.core.upload_limit import UploadBodyLimitMiddleware
from app.modules.workspace.router import router

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_demo_files()
    migrate(engine)
    with SessionLocal() as db:
        seed_workspace(db)
        db.commit()
    yield
    engine.dispose()

app = FastAPI(title="BranchFrame API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=[settings.web_origin],
    allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-Id", "Idempotency-Key"],
    expose_headers=["X-Request-Id", "Idempotent-Replayed", "Retry-After"],
)

app.add_middleware(UploadBodyLimitMiddleware)

@app.middleware("http")
async def request_id(request: Request, call_next):
    incoming = request.headers.get("X-Request-Id", "")
    request.state.request_id = incoming if incoming and len(incoming) <= 100 and incoming.isprintable() else str(uuid4())
    try:
        response = await call_next(request)
    except Exception:
        response = error_response(request, 500, "INTERNAL_ERROR", "An internal error occurred.")
    response.headers["X-Request-Id"] = request.state.request_id
    return response

def error_response(request: Request, status: int, code: str, message: str, details=None):
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message,
        "requestId": getattr(request.state, "request_id", str(uuid4())),
        "details": details or [],
    }})

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details = [{"path": ".".join(str(part) for part in item["loc"]),
                "message": item["msg"], "code": item["type"]} for item in exc.errors()]
    return error_response(request, 400, "VALIDATION_ERROR", "The request contains invalid fields.", details)

@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    code = {404: "NOT_FOUND", 401: "UNAUTHENTICATED", 403: "FORBIDDEN"}.get(exc.status_code, "VALIDATION_ERROR")
    return error_response(request, exc.status_code, code, "Resource not found." if exc.status_code == 404 else "The request cannot be processed.")

@app.get("/api/v1/health")
def health():
    return {"data": {"status": "ok", "apiVersion": "1.0.0", "contractVersion": "1.0.0",
                     "aiMode": settings.ai_mode, "demoMode": settings.demo_mode, "authMode": settings.auth_mode}}

@app.get("/ready", include_in_schema=False)
def ready(request: Request):
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return error_response(request, 503, "INTERNAL_ERROR", "Database is unavailable.")
    return {"status": "ok"}

@app.exception_handler(DomainError)
async def domain_error(request: Request, exc: DomainError):
    return error_response(request, exc.status, exc.code, exc.message, exc.details)

app.include_router(router)

from app.modules.assets.router import router as asset_router
app.include_router(asset_router)

from app.modules.collections.router import router as collection_router
app.include_router(collection_router)

from app.modules.references.router import router as reference_router
app.include_router(reference_router)

from app.modules.assessments.router import router as assessment_router
app.include_router(assessment_router)

from app.modules.assessments.history import router as history_router
app.include_router(history_router)

from app.modules.system.router import router as system_router
app.include_router(system_router)
app.mount("/demo-assets", StaticFiles(directory=str(demo_dir()), check_dir=False), name="demo-assets")
