import csv
import json
import re
from io import StringIO
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from sqlalchemy import select
from app.core.models import Direction, DirectionRevision, BriefRevision, now
from app.core.config import get_settings
from app.core.ai_router import configured
from app.modules.workspace.service import project_dto, direction_dto, revision_dto, actor, iso
from app.modules.assets.models import Asset
from app.modules.assets.service import asset_dto, versions, version_dto
from app.modules.collections.models import Collection
from app.modules.collections.service import revisions, collection_dto, revision_dto as collection_revision_dto
from app.modules.references.models import Reference
from app.modules.references.service import reference_dto
from app.modules.assessments.models import Assessment
from app.modules.assessments.service import assessment_dto, assessment_items
from app.modules.assessments.history import decisions
from app.modules.system.models import ExportRecord

STATUSES = ["DRAFT", "IN_PROGRESS", "NEEDS_REVIEW", "APPROVED", "REJECTED", "ARCHIVED"]

def decision_rows(db, p):
    return decisions(project_id=p.id, db=db, limit=100, cursor=None, assessmentId=None, assetId=None,
        assetVersionId=None, collectionRevisionId=None, decisionType=None, currentOnly=False, sort="createdAt:desc")

def all_decisions(db, p):
    result = []
    cursor = None
    while True:
        page = decisions(project_id=p.id, db=db, limit=100, cursor=cursor, assessmentId=None, assetId=None,
            assetVersionId=None, collectionRevisionId=None, decisionType=None, currentOnly=False, sort="createdAt:desc")
        result.extend(page["data"])
        cursor = page["page"]["nextCursor"]
        if not cursor:
            return result

def summary(db, p):
    assets = list(db.scalars(select(Asset).where(Asset.project_id == p.id)))
    assessments = list(db.scalars(select(Assessment).where(Assessment.project_id == p.id).order_by(Assessment.started_at.desc(), Assessment.id.desc())))
    completed = next((a for a in assessments if a.status == "COMPLETED"), None)
    unresolved = sum(i.resolution_status in ("UNRESOLVED", "DEFERRED") for i in assessment_items(db, completed)) if completed else 0
    stale = 0
    for c in db.scalars(select(Collection).where(Collection.project_id == p.id, Collection.status != "ARCHIVED")):
        latest = next((r for r in revisions(db, c) if r.status != "SUPERSEDED"), None)
        if latest:
            stale += collection_revision_dto(db, latest)["staleItemCount"]
    active = None
    if p.active_direction_revision_id:
        r = db.get(DirectionRevision, p.active_direction_revision_id)
        active = {"direction": direction_dto(db, db.get(Direction, r.direction_id)), "revision": revision_dto(db, r)}
    latest = assessments[0] if assessments else None
    return {"project": project_dto(db, p), "activeDirection": active,
        "assetCounts": {"total": len(assets), "byStatus": {s: sum(a.status == s for a in assets) for s in STATUSES}},
        "unresolvedRecommendationCount": unresolved, "staleCollectionItemCount": stale,
        "latestAssessment": {"id": latest.id, "status": latest.status, "startedAt": iso(latest.started_at),
            "isSimulated": latest.result_metadata["isSimulated"]} if latest else None,
        "recentDecisions": decision_rows(db, p)["data"][:5]}

def ai_settings():
    s = get_settings()
    return {"mode": s.ai_mode, "providerConfigured": bool(s.ai_api_key or s.openai_api_key),
        "modelName": "mock" if s.ai_mode == "mock" else (s.ai_model or s.openai_model or None),
        "imageAnalysisAvailable": s.ai_mode == "mock" or (s.ai_image_analysis_enabled and configured()), "maxAssetsPerAssessment": s.ai_max_assets_per_assessment,
        "monthlyBudgetLimitUsd": s.ai_monthly_budget_limit_usd, "monthToDateCostUsd": None}

def sanitized(value):
    # Provider/env secrets and signed/local storage URLs must not enter exports.
    s = get_settings()
    secrets = [v for v in [s.ai_api_key, s.openai_api_key] if v]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            canonical = re.sub(r"[^a-z]", "", key.lower())
            if canonical in {"apikey", "authorization", "password", "secret", "token", "accesstoken", "storagekey", "signingkey"}:
                continue
            result[key] = sanitized(item)
        return result
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        def clean_url(match):
            url = urlsplit(match.group(0))
            if "/api/v1/files/" in url.path:
                return "[LOCAL_FILE_EXCLUDED]"
            netloc = url.netloc.rsplit("@", 1)[-1]
            return urlunsplit((url.scheme, netloc, url.path, "", ""))
        if value.startswith(("http://", "https://")) and "/api/v1/files/" in urlsplit(value).path:
            return None
        return re.sub(r"https?://[^\s<>\"'\x60]+", clean_url, value)
    return value

def project_export(db, p, format):
    asset_rows = list(db.scalars(select(Asset).where(Asset.project_id == p.id)))
    assets = [{**asset_dto(db, a, detail=True), "versions": [version_dto(db, v) for v in versions(db, a)]} for a in asset_rows]
    collections = [{**collection_dto(db, c, detail=True), "revisions": [collection_revision_dto(db, r, detail=True) for r in revisions(db, c)]}
        for c in db.scalars(select(Collection).where(Collection.project_id == p.id))]
    directions = []
    for d in db.scalars(select(Direction).where(Direction.project_id == p.id)):
        directions.append({**direction_dto(db, d), "revisions": [revision_dto(db, r) for r in db.scalars(select(DirectionRevision).where(DirectionRevision.direction_id == d.id).order_by(DirectionRevision.revision_number))]})
    data = sanitized({"project": project_dto(db, p),
        "briefRevisions": [revision_dto(db, r) for r in db.scalars(select(BriefRevision).where(BriefRevision.project_id == p.id).order_by(BriefRevision.revision_number))],
        "directions": directions,
        "references": [reference_dto(db, r) for r in db.scalars(select(Reference).where(Reference.project_id == p.id))],
        "assets": assets,
        "assessments": [assessment_dto(db, a) for a in db.scalars(select(Assessment).where(Assessment.project_id == p.id))],
        "decisions": all_decisions(db, p), "collections": collections})
    warnings = []
    if any(v.content.get("storageObjectId") or (v.content.get("externalFileUrl") or "").startswith("/") for a in asset_rows for v in versions(db, a)):
        warnings.append({"code": "LOCAL_ONLY_FILE_PATHS", "message": "Local upload links are excluded; demo paths require this server. Media is not bundled."})
    if any(v["isMetadataOnly"] for a in assets for v in a["versions"]):
        warnings.append({"code": "METADATA_ONLY_VERSIONS", "message": "Some versions contain metadata without image files."})
    warnings.append({"code": "OTHER", "message": "URL query parameters and configured credentials are excluded from exports."})
    if format == "JSON":
        content, content_type, extension = json.dumps(data, ensure_ascii=False, indent=2), "application/json", "json"
    elif format == "MARKDOWN":
        # Human-readable sections plus a complete structured appendix.
        content = markdown_report(data)
        content_type, extension = "text/markdown", "md"
    else:
        buffer = StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["assetId", "title", "assetType", "status", "latestVersionId", "versionNumber", "directionRevisionId", "prompt"])
        def safe(value):
            text = str(value) if value is not None else ""
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text
        for a in data["assets"]:
            v = a["versions"][0] if a["versions"] else {}
            writer.writerow([safe(x) for x in [a["id"], a["title"], a["assetType"], a["status"], v.get("id"), v.get("versionNumber"), v.get("directionRevisionId"), v.get("prompt")]])
        content, content_type, extension = buffer.getvalue(), "text/csv", "csv"
    result = {"id": str(uuid4()), "format": format, "filename": p.slug + "." + extension, "contentType": content_type,
        "content": content, "warnings": warnings, "createdAt": iso(now())}
    db.add(ExportRecord(id=result["id"], project_id=p.id, format=format, result=result))
    return result

def markdown_report(data):
    def text(value):
        return str(value or "—").replace("\r", " ").replace("\n", " ").replace("|", "\\|")
    p = data["project"]
    lines = ["# " + text(p["name"]), "", "## Project", "",
        "- Project ID: " + p["id"], "- Status: " + p["status"],
        "- Active direction revision: " + text(p["activeDirectionRevisionId"]),
        "- Assets: " + str(len(data["assets"])), "- Description: " + text(p["description"]), ""]
    lines += ["## Brief history", ""]
    for b in data["briefRevisions"]:
        lines += ["### Brief v" + str(b["revisionNumber"]) + ": " + text(b["title"]), "",
            "- Objective: " + text(b["objective"]), "- Audience: " + text(b["targetAudience"]),
            "- Deliverables: " + text(", ".join(b["deliverables"])),
            "- Change: " + text(b["changeSummary"]), ""]
        for requirement in b["requirements"] + b["forbiddenAttributes"]:
            lines += ["- Requirement: " + text(requirement["label"]) + " — " + text(requirement["value"])]
        for label, key in [("Constraint", "constraints"), ("Acceptance criterion", "acceptanceCriteria")]:
            lines += ["- " + label + ": " + text(value) for value in b[key]]
        lines.append("")
    lines += ["## Direction history", ""]
    for d in data["directions"]:
        lines += ["### " + text(d["name"]), ""]
        for r in d["revisions"]:
            lines += ["#### Revision " + str(r["revisionNumber"]) + " (" + r["id"] + ")", "",
                text(r["summary"]), "", "- Palette: " + text(", ".join(x["name"] for x in r["palette"])),
                "- Lighting: " + text(", ".join(str(x) for x in r["lighting"].values() if x)),
                "- Materials: " + text(", ".join(r["materials"])), "- Mood: " + text(", ".join(r["mood"])),
                "- Style prompt: " + text(r["stylePrompt"]), ""]
    lines += ["## Assets and version lineage", ""]
    for a in data["assets"]:
        lines += ["### " + text(a["title"]), "", "- Asset ID: " + a["id"],
            "- Type / status: " + a["assetType"] + " / " + a["status"], ""]
        for v in reversed(a["versions"]):
            lines += ["#### Version " + str(v["versionNumber"]) + " (" + v["id"] + ")", "",
                "- Status: " + v["status"], "- Parent version: " + text(v["derivedFromVersionId"]),
                "- Direction revision: " + text(v["directionRevisionId"]),
                "- Brief revision: " + text(v["briefRevisionId"]),
                "- Rationale: " + text(v["revisionRationale"]),
                "- Prompt: " + text(v["prompt"]), "- Negative prompt: " + text(v["negativePrompt"]),
                "- Model: " + text(v["modelName"]), ""]
            for key, values in v["recordedAttributes"].items():
                if values:
                    lines += ["- " + key + ": " + text(", ".join(values))]
            lines.append("")
    lines += ["## References", ""]
    for r in data["references"]:
        lines += ["- " + text(r["title"]) + " (" + r["id"] + ") — " + text(r["usageRightsNote"])]
    lines += ["", "## Impact assessments", ""]
    for a in data["assessments"]:
        lines += ["### " + a["id"], "", "- Status: " + a["status"],
            "- Direction change: " + text(a["oldDirectionRevisionId"]) + " → " + a["newDirectionRevisionId"],
            "- Mode: " + a["mode"] + (" (simulated)" if a["isSimulated"] else ""),
            "- Summary: " + text(a["summary"]), ""]
        for i in a["items"] or []:
            lines += ["- " + i["assetVersion"]["id"] + ": " + i["recommendation"] +
                " → effective " + i["effectiveRecommendation"] + " / " + i["resolutionStatus"],
                "  - Reason: " + text(i.get("rationale")),
                "  - Human review: " + str(i["requiresHumanReview"])]
        lines.append("")
    lines += ["## Collections and exact version pins", ""]
    for c in data["collections"]:
        lines += ["### " + text(c["name"]), ""]
        for r in c["revisions"]:
            lines += ["#### Revision " + str(r["revisionNumber"]) + " / " + r["status"], "",
                "| Position | Asset version | Stale |", "| --- | --- | --- |"]
            for i in r["items"]:
                lines += ["| " + str(i["position"]) + " | " + i["pinnedVersion"]["id"] + " | " + str(i["isStale"]) + " |"]
            lines.append("")
    lines += ["## Decision history", ""]
    for d in data["decisions"]:
        lines += ["- " + d["createdAt"] + " — " + d["decisionType"] + " by " + text(d["createdBy"]["displayName"]),
            "  - Rationale: " + text(d["rationale"]),
            "  - Supersedes: " + text(d["supersedesDecisionId"])]
    lines += ["", "## Complete structured record", "",
        "The record below preserves all metadata, evidence and exact version IDs. Media files are not bundled.", "",
        "~~~json", json.dumps(data, ensure_ascii=False, indent=2).replace("~~~", "\\u007e\\u007e\\u007e"), "~~~", ""]
    return "\n".join(lines)
