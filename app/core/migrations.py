from sqlalchemy import text
from app.core.models import Base
from app.modules.assets import models as asset_models
from app.modules.collections import models as collection_models
from app.modules.references import models as reference_models
from app.modules.assessments import models as assessment_models
from app.modules.system import models as system_models

WORKSPACE_TABLES = ["users", "projects", "brief_revisions", "directions", "direction_revisions", "decisions", "activity_events", "idempotency_records"]
ASSET_TABLES = ["assets", "asset_versions", "asset_version_decisions"]

def migrate(engine):
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(702611)"))
        connection.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version VARCHAR(40) PRIMARY KEY)"))
        for version, tables in [("001_workspace", WORKSPACE_TABLES), ("002_assets", ASSET_TABLES), ("003_collections", ["collections", "collection_revisions", "collection_items", "collection_approvals", "collection_pin_decisions"]), ("004_references", ["storage_objects", "references", "reference_attributes", "reference_links"]), ("005_assessments", ["impact_assessments", "impact_assessment_items", "assessment_decisions"]), ("006_exports", ["exports"])]:
            applied = connection.execute(text("SELECT version FROM schema_migrations WHERE version = :version"), {"version": version}).first()
            if not applied:
                Base.metadata.create_all(connection, tables=[Base.metadata.tables[name] for name in tables])
                connection.execute(text("INSERT INTO schema_migrations (version) VALUES (:version)"), {"version": version})
