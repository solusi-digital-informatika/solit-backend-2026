
import csv
import json
from io import StringIO
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
import app.main as main
from app.core.seed import seed_workspace
from app.modules.assets.models import AssetVersion
from app.modules.assessments import provider
from app.modules.system.models import ExportRecord

def ident(n):
    return "00000000-0000-4000-8000-" + str(n).zfill(12)

P, A, B = ident(10), ident(31), ident(41)

def call(client, method, path, **kwargs):
    response = client.request(method, "/api/v1" + path, **kwargs)
    assert response.is_success, response.text
    return response.json()["data"]

@pytest.fixture
def seeded(client):
    with main.SessionLocal() as db:
        seed_workspace(db)
        db.commit()
    return client

def assess(client, key=None, **extra):
    return call(client, "POST", "/projects/" + P + "/impact-assessments",
        headers={"Idempotency-Key": key or str(uuid4())},
        json={"oldDirectionRevisionId": A, "newDirectionRevisionId": B, "mode": "RULES_ONLY", **extra})

def test_seed_exact_ids_restart_and_template(seeded):
    s = call(seeded, "GET", "/projects/" + P + "/summary")
    assert s["assetCounts"]["total"] == 4
    assert s["assetCounts"]["byStatus"]["APPROVED"] == 3
    assert s["activeDirection"]["revision"]["id"] == A
    assert s["latestAssessment"] is None and s["staleCollectionItemCount"] == 0
    assert s["unresolvedRecommendationCount"] == 0 and len(s["recentDecisions"]) == 5
    r = call(seeded, "GET", "/collection-revisions/" + ident(71))
    assert [i["pinnedVersion"]["id"] for i in r["items"]] == [ident(61), ident(62), ident(63)]
    assert all(not i["isStale"] for i in r["items"])
    assert seeded.get("/demo-assets/hero-v1.jpg").status_code == 200
    with main.SessionLocal() as db:
        seed_workspace(db)
        db.commit()
    assert call(seeded, "GET", "/projects/" + P + "/summary") == s
    cloned = call(seeded, "POST", "/projects", json={"name": "Solara clone", "template": "DEMO_SOLARA"})
    assets = call(seeded, "GET", "/projects/" + cloned["id"] + "/assets")
    assert len(assets) == 4 and all(x["id"] not in [ident(n) for n in range(50, 54)] for x in assets)

def test_full_direction_version_collection_workflow(seeded):
    original = call(seeded, "GET", "/collection-revisions/" + ident(71))
    call(seeded, "POST", "/projects/" + P + "/active-direction",
        json={"directionRevisionId": B, "reason": "Client requests warm organic direction"})
    key = str(uuid4())
    a = assess(seeded, key)
    replay = seeded.post("/api/v1/projects/" + P + "/impact-assessments",
        headers={"Idempotency-Key": key},
        json={"oldDirectionRevisionId": A, "newDirectionRevisionId": B, "mode": "RULES_ONLY"})
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert [i["assetVersion"]["id"] for i in a["items"]] == [ident(63), ident(64), ident(62), ident(61)]
    assert [i["recommendation"] for i in a["items"]] == ["REVIEW_REQUIRED", "RECREATE_CANDIDATE", "ADAPT_CANDIDATE", "REUSE_CANDIDATE"]
    item = a["items"][1]
    decision = call(seeded, "POST", "/impact-assessments/" + a["id"] + "/items/" + item["id"] + "/decision",
        json={"resolution": "OVERRIDE", "overrideRecommendation": "REVIEW_REQUIRED", "rationale": "Review material suitability first"})
    assert decision["item"]["recommendation"] == "RECREATE_CANDIDATE"
    assert decision["item"]["effectiveRecommendation"] == "REVIEW_REQUIRED"
    s = call(seeded, "GET", "/projects/" + P + "/summary")
    assert s["unresolvedRecommendationCount"] == 3 and s["latestAssessment"]["id"] == a["id"]
    v = call(seeded, "POST", "/assets/" + ident(51) + "/versions",
        json={"derivedFromVersionId": ident(62), "metadataOnly": True, "revisionRationale": "Adapt for new direction"})
    stale = call(seeded, "GET", "/collection-revisions/" + ident(71))
    old = stale["items"][1]
    assert old["pinnedVersion"]["id"] == ident(62) and old["isStale"]
    assert call(seeded, "GET", "/projects/" + P + "/summary")["staleCollectionItemCount"] == 1
    patch = {"assetVersionId": v["id"], "rationale": "Pin adapted version explicitly"}
    frozen = seeded.patch("/api/v1/collection-revision-items/" + old["id"], json=patch)
    assert frozen.status_code == 409 and frozen.json()["error"]["code"] == "REVISION_FROZEN"
    r2 = call(seeded, "POST", "/collections/" + ident(70) + "/revisions", json={"changeSummary": "Apply adapted close-up"})
    assert r2["items"][1]["pinnedVersion"]["id"] == ident(62)
    call(seeded, "PATCH", "/collection-revision-items/" + r2["items"][1]["id"], json=patch)
    call(seeded, "POST", "/collection-revisions/" + r2["id"] + "/submit-review")
    call(seeded, "POST", "/collection-revisions/" + r2["id"] + "/approve", json={})
    history = call(seeded, "GET", "/collection-revisions/" + ident(71))
    assert history["status"] == "SUPERSEDED"
    assert [i["pinnedVersion"]["id"] for i in history["items"]] == [i["pinnedVersion"]["id"] for i in original["items"]]
    assert call(seeded, "GET", "/projects/" + P + "/summary")["staleCollectionItemCount"] == 0
    events = call(seeded, "GET", "/projects/" + P + "/activity?limit=100")
    assert {"ASSESSMENT_COMPLETED", "RECOMMENDATION_RESOLVED", "COLLECTION_ITEM_REPLACED"} <= {e["eventType"] for e in events}

def test_summary_latest_failed_uses_last_completed(seeded, monkeypatch):
    a = assess(seeded)
    def fail(*args):
        raise RuntimeError("provider failure")
    monkeypatch.setattr(provider, "analyze", fail)
    failed = assess(seeded)
    assert failed["status"] == "FAILED"
    s = call(seeded, "GET", "/projects/" + P + "/summary")
    assert s["latestAssessment"]["id"] == failed["id"]
    assert s["unresolvedRecommendationCount"] == 4

@pytest.mark.parametrize("confirm", [False, 1, "true", None])
def test_reset_requires_explicit_true(seeded, confirm):
    assert seeded.post("/api/v1/demo/reset", json={"confirm": confirm}).status_code == 400
    assert call(seeded, "GET", "/projects/" + P + "/summary")["assetCounts"]["total"] == 4

def test_reset_scoped_preserves_other_project_and_idempotency(seeded, monkeypatch):
    other = call(seeded, "POST", "/projects", headers={"Idempotency-Key": "unrelated-project"},
        json={"name": "Keep my project", "template": "DEMO_SOLARA"})
    other_before = call(seeded, "GET", "/projects/" + other["id"] + "/assets")
    key = "reset-assessment-key"
    a = assess(seeded, key)
    call(seeded, "POST", "/assets/" + ident(51) + "/versions",
        json={"metadataOnly": True, "revisionRationale": "Temporary version"})
    call(seeded, "POST", "/projects/" + P + "/exports", json={"format": "JSON"})
    reset = call(seeded, "POST", "/demo/reset", json={"confirm": True})
    assert reset["id"] == P and reset["activeDirectionRevisionId"] == A
    assert call(seeded, "GET", "/projects/" + other["id"] + "/assets") == other_before
    assert call(seeded, "GET", "/projects/" + P + "/summary")["assetCounts"]["total"] == 4
    assert seeded.get("/api/v1/impact-assessments/" + a["id"]).status_code == 404
    assert assess(seeded, key)["id"] != a["id"]
    replay = seeded.post("/api/v1/projects", headers={"Idempotency-Key": "unrelated-project"},
        json={"name": "Keep my project", "template": "DEMO_SOLARA"})
    assert replay.status_code == 200 and replay.json()["data"]["id"] == other["id"]
    with main.SessionLocal() as db:
        assert list(db.scalars(select(ExportRecord))) == []
    monkeypatch.setattr(main.settings, "demo_mode", False)
    assert seeded.post("/api/v1/demo/reset", json={"confirm": True}).status_code == 403

@pytest.mark.parametrize("format", ["JSON", "MARKDOWN", "CSV"])
def test_export_structure_credentials_and_csv_injection(seeded, monkeypatch, format):
    secret = "test-secret-do-not-export"
    monkeypatch.setattr(main.settings, "ai_api_key", secret)
    call(seeded, "POST", "/projects/" + P + "/assets", json={
        "title": "=HYPERLINK(test)", "assetType": "OTHER",
        "initialVersion": {"metadataOnly": True, "revisionRationale": "Security export case",
            "prompt": "Prompt " + secret,
            "generationSettings": {"apiKey": secret, "storageKey": "private-storage-path", "seed": 42}}})
    assess(seeded)
    result = call(seeded, "POST", "/projects/" + P + "/exports", json={"format": format})
    assert secret not in result["content"] and "private-storage-path" not in result["content"]
    assert {"LOCAL_ONLY_FILE_PATHS", "METADATA_ONLY_VERSIONS"} <= {w["code"] for w in result["warnings"]}
    if format == "JSON":
        data = json.loads(result["content"])
        assert len(data["assets"]) == 5 and len(data["assets"][0]["versions"]) == 2
        assert len(data["assessments"][0]["items"]) == 5
        assert data["collections"][0]["revisions"][0]["items"][0]["pinnedVersion"]["id"] == ident(61)
    elif format == "MARKDOWN":
        assert "## Direction history" in result["content"] and "## Decision history" in result["content"]
        assert "## Collections and exact version pins" in result["content"]
    else:
        rows = list(csv.DictReader(StringIO(result["content"])))
        assert len(rows) == 5 and rows[-1]["title"].startswith("'=")
    with main.SessionLocal() as db:
        assert db.get(ExportRecord, result["id"]).result == result

def test_uploaded_urls_excluded_from_export(seeded):
    from tests.test_references import upload
    uploaded = upload(seeded)
    call(seeded, "POST", "/projects/" + P + "/assets", json={"title": "Uploaded", "assetType": "OTHER",
        "initialVersion": {"storageObjectId": uploaded["id"], "revisionRationale": "Use uploaded file"}})
    result = call(seeded, "POST", "/projects/" + P + "/exports", json={"format": "JSON"})
    assert "signature=" not in result["content"] and "/api/v1/files/" not in result["content"]

def test_settings_and_generic_router_connection(seeded, monkeypatch):
    s = main.settings
    monkeypatch.setattr(s, "ai_api_key", "router-secret")
    monkeypatch.setattr(s, "ai_base_url", "https://router.example/v1/")
    monkeypatch.setattr(s, "ai_model", "provider/gemini-model")
    settings = call(seeded, "GET", "/settings/ai")
    assert settings["providerConfigured"] and settings["mode"] == "mock"
    assert "router-secret" not in json.dumps(settings)
    assert call(seeded, "POST", "/settings/ai/test")["ok"]
    monkeypatch.setattr(s, "ai_mode", "openai")
    assert call(seeded, "GET", "/settings/ai")["modelName"] == "provider/gemini-model"
    def post(url, **kwargs):
        assert url == "https://router.example/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer router-secret"
        assert kwargs["json"]["model"] == "provider/gemini-model"
        assert kwargs["json"]["max_tokens"] == 8
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]},
            request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    assert call(seeded, "POST", "/settings/ai/test")["ok"]
    monkeypatch.setattr(s, "ai_api_key", "")
    monkeypatch.setattr(s, "openai_api_key", "")
    assert seeded.post("/api/v1/settings/ai/test").status_code == 422

@pytest.mark.parametrize("failure,code", [("timeout", "PROVIDER_TIMEOUT"), ("error", "PROVIDER_ERROR"), ("malformed", "PROVIDER_ERROR")])
def test_router_errors_do_not_expose_secrets(seeded, monkeypatch, failure, code):
    for key, value in {"ai_mode": "openai", "ai_api_key": "private-key", "ai_model": "any/model", "ai_base_url": "https://router.example/v1"}.items():
        monkeypatch.setattr(main.settings, key, value)
    def post(url, **kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("private-key")
        if failure == "error":
            raise RuntimeError("private-key")
        return httpx.Response(200, json={"choices": []}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    response = seeded.post("/api/v1/settings/ai/test")
    assert response.json()["error"]["code"] == code and "private-key" not in response.text

def test_restart_preserves_customized_demo_directions(seeded):
    d = call(seeded, "GET", "/directions/" + ident(30))
    call(seeded, "PATCH", "/directions/" + ident(30),
        json={"name": "Client renamed direction", "expectedUpdatedAt": d["updatedAt"]})
    with main.SessionLocal() as db:
        seed_workspace(db)
        db.commit()
    assert call(seeded, "GET", "/directions/" + ident(30))["name"] == "Client renamed direction"

def test_reset_keeps_uploaded_files(seeded):
    from tests.test_references import upload
    from urllib.parse import urlsplit
    uploaded = upload(seeded)
    call(seeded, "POST", "/demo/reset", json={"confirm": True})
    parsed = urlsplit(uploaded["url"])
    assert seeded.get(parsed.path + "?" + parsed.query).status_code == 200

def test_export_removes_embedded_signed_urls_and_credentials(seeded):
    call(seeded, "POST", "/projects/" + P + "/assets", json={
        "title": "Inline links", "assetType": "OTHER",
        "initialVersion": {"metadataOnly": True, "revisionRationale": "Check sanitization",
            "prompt": "Image http://localhost:8000/api/v1/files/abc?signature=private-signature "
                "and https://user:private-password@example.com/image.png?token=private-token"}})
    result = call(seeded, "POST", "/projects/" + P + "/exports", json={"format": "JSON"})
    assert all(value not in result["content"] for value in ["private-signature", "private-password", "private-token"])
    assert "https://example.com/image.png" in result["content"]
