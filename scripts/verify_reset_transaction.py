"""Validate PostgreSQL reset/FK behavior; roll back every change."""
from types import SimpleNamespace
from sqlalchemy import select, func
from app.core.database import SessionLocal
from app.core.demo import reset_demo
from app.core.models import Project
from app.modules.workspace.service import DEMO_PROJECT
from app.modules.assets.models import AssetVersion
from app.modules.system.service import summary

def main():
    with SessionLocal() as db:
        before = [(p.id, p.name, p.updated_at) for p in db.scalars(select(Project).where(Project.id != DEMO_PROJECT))]
        versions_before = db.scalar(select(func.count()).select_from(AssetVersion))
        try:
            p = reset_demo(db, SimpleNamespace(state=SimpleNamespace(request_id="reset-transaction-check")))
            assert summary(db, p)["assetCounts"]["total"] == 4
            assert [(p.id, p.name, p.updated_at) for p in db.scalars(select(Project).where(Project.id != DEMO_PROJECT))] == before
            db.flush()
        finally:
            db.rollback()
        assert db.scalar(select(func.count()).select_from(AssetVersion)) == versions_before
        print("PostgreSQL demo reset verified with transaction rollback; existing demo and other projects preserved.")

if __name__ == "__main__":
    main()
