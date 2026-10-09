import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
import app.main as main
from app.core.database import get_db
from app.core.models import Base

@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    # Existing module tests use an empty workspace; system tests explicitly add the full seed.
    from app.core.seed import seed_workspace as original_seed
    monkeypatch.setattr(main, "seed_workspace", lambda db: original_seed(db, complete=False))
    monkeypatch.setattr(main, "ensure_demo_files", lambda: None)
    # Unit/API tests never inherit real provider credentials from developer .env.
    for key, value in {"ai_mode": "mock", "ai_api_key": "", "openai_api_key": "",
        "ai_base_url": "", "ai_model": "", "openai_model": "",
        "ai_provider_name": "router", "ai_image_analysis_enabled": False}.items():
        monkeypatch.setattr(main.settings, key, value)
    monkeypatch.setattr(main.settings, "storage_local_dir", str(tmp_path / "uploads"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", factory)
    def session():
        with factory() as db:
            yield db
    main.app.dependency_overrides[get_db] = session
    yield
    main.app.dependency_overrides.clear()
    engine.dispose()

@pytest.fixture
def client():
    with TestClient(main.app) as client:
        yield client
