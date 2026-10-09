from uuid import uuid4
import pytest
from sqlalchemy import select
import app.main as main
from app.modules.assessments import provider
from app.modules.assessments.models import Assessment, AssessmentItem, AssessmentDecision
from app.modules.assessments.schemas import AssessmentDTO
from app.modules.assessments.rules import assess

P = "00000000-0000-4000-8000-000000000010"
A = "00000000-0000-4000-8000-000000000031"
B = "00000000-0000-4000-8000-000000000041"

def assets(client):
    styles = [
        ("Factory", {"palette": ["graphite"], "materials": ["metal"]}),
        ("Metal", {"palette": ["graphite"], "lighting": ["hard"], "materials": ["metal"], "mood": ["technical"], "composition": ["macro"]}),
        ("Close-up", {"palette": ["graphite"], "lighting": ["hard"], "materials": ["wood"], "mood": ["calm"], "composition": ["product close-up"]}),
        ("Hero", {"palette": ["cream"], "lighting": ["soft natural light"], "materials": ["wood"], "mood": ["calm"], "composition": ["hero"]}),
    ]
    result = []
    for title, recorded in styles:
        response = client.post("/api/v1/projects/" + P + "/assets", json={"title": title, "assetType": "OTHER",
            "initialVersion": {"metadataOnly": True, "recordedAttributes": recorded, "revisionRationale": "Initial version"}})
        assert response.status_code == 201
        result.append(response.json()["data"])
    return result

def start(client, mode="RULES_ONLY", key=None, **extra):
    return client.post("/api/v1/projects/" + P + "/impact-assessments", headers={"Idempotency-Key": key or str(uuid4())},
        json={"newDirectionRevisionId": B, "mode": mode, **extra})

def test_four_outcomes_and_response(client):
    created = assets(client)
    response = start(client)
    assert response.status_code == 201, response.text
    a = response.json()["data"]
    AssessmentDTO.model_validate(a)
    assert a["status"] == "COMPLETED" and not a["isSimulated"] and a["providerName"] is None
    assert [i["recommendation"] for i in a["items"]] == ["REVIEW_REQUIRED", "RECREATE_CANDIDATE", "ADAPT_CANDIDATE", "REUSE_CANDIDATE"]
    assert [i["priority"] for i in a["items"]] == [1, 2, 3, 4]
    assert a["counts"]["unresolved"] == 4 and all(x == 1 for x in a["counts"]["byRecommendation"].values())
    assert a["items"][0]["uncertaintyLevel"] == "HIGH"
    assert a["items"][-1]["requiresHumanReview"] is False
    assert a["imagesSent"] == 0
    assert all(i["supportingEvidence"] or i["conflictingEvidence"] for i in a["items"])

def test_mock_label_and_no_image_claim(client):
    assets(client)
    for mode in ("AI_ASSISTED", "HYBRID"):
        a = start(client, mode, includeImages=True).json()["data"]
        assert a["isSimulated"] and a["providerName"] == "mock" and a["modelName"] == "mock"
        assert a["imagesSent"] == 0
        assert all(e["source"] != "IMAGE_OBSERVATION" for i in a["items"] for e in i["supportingEvidence"] + i["conflictingEvidence"])

def test_override_supersedes_without_mutation(client):
    created = assets(client)
    a = start(client).json()["data"]
    item = a["items"][1]
    version_url = "/api/v1/asset-versions/" + item["assetVersion"]["id"]
    old = client.get(version_url).json()
    path = "/api/v1/impact-assessments/" + a["id"] + "/items/" + item["id"] + "/decision"
    override = client.post(path, json={"resolution": "OVERRIDE", "overrideRecommendation": "REVIEW_REQUIRED", "rationale": "Needs expert review"})
    assert override.status_code == 201
    result = override.json()["data"]
    assert result["item"]["recommendation"] == "RECREATE_CANDIDATE"
    assert result["item"]["effectiveRecommendation"] == "REVIEW_REQUIRED"
    accepted = client.post(path, json={"resolution": "ACCEPT"}).json()["data"]
    assert accepted["decision"]["supersedesDecisionId"] == result["decision"]["id"]
    assert accepted["item"]["recommendation"] == accepted["item"]["effectiveRecommendation"] == "RECREATE_CANDIDATE"
    assert accepted["decision"]["rationale"] == "Accepted without comment"
    assert client.get(version_url).json() == old
    with main.SessionLocal() as db:
        assert len(list(db.scalars(select(AssessmentDecision)))) == 2
    assert client.post(path, json={"resolution": "DISMISS"}).status_code == 400
    assert client.post(path, json={"resolution": "OVERRIDE", "overrideRecommendation": "RECREATE_CANDIDATE", "rationale": "No change"}).status_code == 400

def test_idempotency_scope_and_filter(client):
    created = assets(client)
    key = str(uuid4())
    one = start(client, key=key)
    two = start(client, key=key)
    assert one.status_code == 201 and two.status_code == 200 and one.json() == two.json()
    assert two.headers["Idempotent-Replayed"] == "true"
    assert start(client, mode="HYBRID", key=key).status_code == 422
    a = one.json()["data"]
    filtered = client.get("/api/v1/impact-assessments/" + a["id"] + "?recommendation=ADAPT_CANDIDATE").json()["data"]
    assert len(filtered["items"]) == 1 and filtered["counts"]["total"] == 4
    listing = client.get("/api/v1/projects/" + P + "/impact-assessments").json()["data"]
    assert len(listing) == 1 and listing[0]["items"] is None

@pytest.mark.parametrize("fault", ["foreign_id", "invalid_enum", "missing_item", "bad_evidence", "image_claim"])
def test_invalid_provider_output_failed_without_items(client, monkeypatch, fault):
    assets(client)
    def invalid_output(snapshot, mode):
        result = assess(snapshot, mode)
        if fault == "foreign_id":
            result[0]["assetVersionId"] = str(uuid4())
        elif fault == "invalid_enum":
            result[0]["recommendation"] = "UNKNOWN"
        elif fault == "missing_item":
            result.pop()
        elif fault == "bad_evidence":
            result[0]["supportingEvidence"][0]["sourceId"] = str(uuid4())
        else:
            result[0]["supportingEvidence"][0]["source"] = "IMAGE_OBSERVATION"
        return result
    monkeypatch.setattr(provider, "analyze", invalid_output)
    response = start(client, mode="AI_ASSISTED")
    assert response.status_code == 201
    a = response.json()["data"]
    assert a["status"] == "FAILED" and a["errorCode"] == "PROVIDER_ERROR" and a["items"] is None
    with main.SessionLocal() as db:
        assert list(db.scalars(select(AssessmentItem))) == []

def test_failure_retry_uses_original_scope(client, monkeypatch):
    created = assets(client)
    original = provider.analyze
    def fail(*args):
        raise TimeoutError("Secret exception detail")
    monkeypatch.setattr(provider, "analyze", fail)
    a = start(client, mode="HYBRID").json()["data"]
    assert a["status"] == "FAILED" and "Secret" not in a["errorSummary"]
    monkeypatch.setattr(provider, "analyze", original)
    client.post("/api/v1/assets/" + created[0]["id"] + "/versions", json={"metadataOnly": True, "revisionRationale": "Newer version"})
    retried = client.post("/api/v1/impact-assessments/" + a["id"] + "/retry").json()["data"]
    assert retried["status"] == "COMPLETED" and retried["id"] == a["id"]
    assert {i["assetVersion"]["id"] for i in retried["items"]} == {x["latestVersion"]["id"] for x in created}
    assert client.post("/api/v1/impact-assessments/" + a["id"] + "/retry").status_code == 409
    assert client.post("/api/v1/impact-assessments/" + a["id"] + "/cancel").status_code == 409

def test_input_limits_and_direction_default(client, monkeypatch):
    created = assets(client)
    assert client.post("/api/v1/projects/" + P + "/impact-assessments", json={"mode": "RULES_ONLY", "newDirectionRevisionId": B}).status_code == 400
    assert start(client, newDirectionRevisionId=A).status_code == 400
    monkeypatch.setattr(main.settings, "ai_max_assets_per_assessment", 1)
    assert start(client, mode="AI_ASSISTED").json()["error"]["code"] == "ASSESSMENT_SCOPE_TOO_LARGE"
    assert start(client).status_code == 201
    monkeypatch.setattr(main.settings, "ai_mode", "openai")
    assert start(client, mode="AI_ASSISTED").json()["error"]["code"] == "AI_NOT_CONFIGURED"
    client.post("/api/v1/projects/" + P + "/active-direction", json={"directionRevisionId": B, "reason": "Switch first"})
    assert start(client).status_code == 400
    assert start(client, oldDirectionRevisionId=A).status_code == 201

def test_cross_project_version_and_explicit_scope(client):
    created = assets(client)
    other = client.post("/api/v1/projects", json={"name": "Another", "template": "BLANK"}).json()["data"]
    foreign = client.post("/api/v1/projects/" + other["id"] + "/assets", json={"title": "Foreign", "assetType": "OTHER",
        "initialVersion": {"metadataOnly": True, "revisionRationale": "Foreign version"}}).json()["data"]
    assert start(client, assetVersionIds=[foreign["latestVersion"]["id"]]).status_code == 422
    one = start(client, assetVersionIds=[created[0]["latestVersion"]["id"]]).json()["data"]
    assert one["counts"]["total"] == 1
    assert start(client, assetVersionIds=[created[0]["latestVersion"]["id"]] * 2).status_code == 400

def test_cancel_pending_state(client):
    assets(client)
    a = start(client).json()["data"]
    # Inline rules/mock finish immediately; inject a pending run to test the future job-state endpoint.
    with main.SessionLocal() as db:
        record = db.get(Assessment, a["id"])
        record.status = "PENDING"
        db.commit()
    response = client.post("/api/v1/impact-assessments/" + a["id"] + "/cancel")
    assert response.json()["data"]["status"] == "CANCELLED"
    assert client.post("/api/v1/impact-assessments/" + a["id"] + "/cancel").status_code == 409

def test_hard_exclusion_and_missing_requirement(client):
    created = assets(client)
    client.post("/api/v1/directions/00000000-0000-4000-8000-000000000040/revisions", json={
        "summary": "Organic", "changeSummary": "Hard constraints",
        "forbiddenAttributes": [{"category": "MATERIAL", "label": "No metal", "value": "metal", "hard": True}],
        "requiredAttributes": [{"category": "SUBJECT", "label": "Product visible", "value": "product", "hard": True}]})
    revision = client.get("/api/v1/directions/00000000-0000-4000-8000-000000000040").json()["data"]["latestRevision"]["id"]
    a = start(client, newDirectionRevisionId=revision).json()["data"]
    item = next(i for i in a["items"] if i["assetVersion"]["id"] == created[1]["latestVersion"]["id"])
    assert item["recommendation"] == "RECREATE_CANDIDATE"
    assert any(e["ruleId"] == "R-FORBIDDEN-CONFLICT" for e in item["conflictingEvidence"])

def test_project_decision_and_activity_history(client):
    assets(client)
    a = start(client).json()["data"]
    item = a["items"][1]
    path = "/api/v1/impact-assessments/" + a["id"] + "/items/" + item["id"] + "/decision"
    one = client.post(path, json={"resolution": "OVERRIDE", "overrideRecommendation": "REVIEW_REQUIRED", "rationale": "Review instead"}).json()["data"]
    two = client.post(path, json={"resolution": "DEFER"}).json()["data"]
    rows = client.get("/api/v1/projects/" + P + "/decisions", params={"assessmentId": a["id"]}).json()["data"]
    assert len(rows) == 2
    first = next(d for d in rows if d["id"] == one["decision"]["id"])
    assert first["supersededByDecisionId"] == two["decision"]["id"]
    current = client.get("/api/v1/projects/" + P + "/decisions", params={"assessmentId": a["id"], "currentOnly": True}).json()["data"]
    assert len(current) == 1 and current[0]["id"] == two["decision"]["id"]
    recent = client.get("/api/v1/assets/" + item["assetVersion"]["assetId"]).json()["data"]["recentDecisions"]
    assert recent[0]["id"] == two["decision"]["id"]
    events = client.get("/api/v1/projects/" + P + "/activity?eventType=RECOMMENDATION_RESOLVED").json()["data"]
    assert len(events) == 2 and all(e["requestId"] for e in events)

