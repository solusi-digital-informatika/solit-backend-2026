from app.modules.assets.schemas import AssetDetail, VersionDTO

P = "00000000-0000-4000-8000-000000000010"
A = "00000000-0000-4000-8000-000000000031"
B = "00000000-0000-4000-8000-000000000041"

def register(client, project=P, version=True):
    body = {"title": "Product hero", "assetType": "HERO_IMAGE", "tags": ["hero", "campaign"]}
    if version:
        body["initialVersion"] = {"metadataOnly": True, "revisionRationale": "Initial version",
            "prompt": "Natural product composition", "recordedAttributes": {"palette": [" CREAM ", "cream"], "mood": [" Calm "]}}
    response = client.post("/api/v1/projects/" + project + "/assets", json=body)
    assert response.status_code == 201, response.text
    AssetDetail.model_validate(response.json()["data"])
    return response.json()["data"]

def test_asset_empty_and_initial_version(client):
    empty = register(client, version=False)
    assert empty["versionCount"] == 0 and empty["latestVersion"] is None
    a = register(client)
    v = client.get("/api/v1/asset-versions/" + a["latestVersion"]["id"]).json()["data"]
    VersionDTO.model_validate(v)
    assert v["recordedAttributes"]["palette"] == ["cream"]
    assert v["directionRevisionId"] == A
    assert v["isMetadataOnly"] and v["modelVersion"] is None and v["fileUrl"] is None

def test_immutable_approval_and_lineage(client):
    a = register(client)
    vid = a["latestVersion"]["id"]
    decision = client.post("/api/v1/asset-versions/" + vid + "/decisions",
        json={"decisionType": "APPROVE_VERSION", "rationale": "Ready for production"})
    assert decision.status_code == 201
    response = client.post("/api/v1/assets/" + a["id"] + "/versions", json={
        "metadataOnly": True, "derivedFromVersionId": vid, "revisionRationale": "New composition", "prompt": "A different prompt"})
    assert response.status_code == 201
    new = response.json()["data"]
    old = client.get("/api/v1/asset-versions/" + vid).json()["data"]
    assert old["status"] == "APPROVED" and old["prompt"] == "Natural product composition"
    assert new["versionNumber"] == 2 and new["lineage"]["derivedFrom"]["id"] == vid
    assert old["lineage"]["derivedVersions"][0]["id"] == new["id"]
    assert old["isLatest"] is False and new["isLatest"] is True
    assert len(client.get("/api/v1/assets/" + a["id"]).json()["data"]["recentDecisions"]) == 1

def test_cross_asset_and_project(client):
    a, b = register(client), register(client)
    bad = client.post("/api/v1/assets/" + a["id"] + "/versions", json={
        "metadataOnly": True, "derivedFromVersionId": b["latestVersion"]["id"], "revisionRationale": "Wrong parent"})
    assert bad.status_code == 400
    p = client.post("/api/v1/projects", json={"name": "Other", "template": "BLANK"}).json()["data"]
    other = register(client, project=p["id"])
    bad = client.post("/api/v1/assets/" + other["id"] + "/versions", json={
        "metadataOnly": True, "directionRevisionId": A, "revisionRationale": "Foreign context"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "CROSS_PROJECT_REFERENCE"

def test_validation_and_atomic_creation(client):
    path = "/api/v1/projects/" + P + "/assets"
    before = len(client.get(path).json()["data"])
    failed = client.post(path, json={"title": "Rollback", "assetType": "OTHER", "initialVersion":{
        "storageObjectId": "00000000-0000-4000-8000-000000000099", "revisionRationale": "Missing file"}})
    assert failed.status_code == 404
    assert len(client.get(path).json()["data"]) == before
    a = register(client)
    url = "/api/v1/assets/" + a["id"] + "/versions"
    for body in [{"revisionRationale": "No file"}, {"revisionRationale": "Unsafe", "externalFileUrl": "javascript:alert(1)"},
                 {"revisionRationale": "Unsafe", "externalFileUrl": "/demo-assets/../secret"},
                 {"revisionRationale": "Invalid width", "externalFileUrl": "https://example.com/a.png", "width": -1}]:
        assert client.post(url, json=body).status_code == 400

def test_asset_edit_conflict_archive_and_filters(client):
    a = register(client)
    path = "/api/v1/assets/" + a["id"]
    bad = client.patch(path, json={"status": "APPROVED", "expectedUpdatedAt": a["updatedAt"]})
    assert bad.status_code == 409
    changed = client.patch(path, json={"status": "ARCHIVED", "expectedUpdatedAt": a["updatedAt"]})
    assert changed.status_code == 200
    assert client.patch(path, json={"title": "Stale", "expectedUpdatedAt": a["updatedAt"]}).status_code == 409
    listpath = "/api/v1/projects/" + P + "/assets"
    assert client.get(listpath).json()["data"] == []
    assert len(client.get(listpath + "?includeArchived=true&tag=hero").json()["data"]) == 1

def test_version_status_transitions(client):
    a = register(client)
    path = "/api/v1/asset-versions/" + a["latestVersion"]["id"]
    assert client.patch(path, json={"status": "NEEDS_REVIEW", "reason": "Skip state"}).status_code == 409
    assert client.patch(path, json={"status": "CANDIDATE", "reason": "Ready to review", "prompt": "Ignored"}).status_code == 200
    assert client.get(path).json()["data"]["prompt"] == "Natural product composition"
    assert client.patch(path, json={"status": "NEEDS_REVIEW", "reason": "Review next"}).status_code == 200
    assert client.patch(path, json={"status": "APPROVED", "reason": "Invalid approval"}).status_code == 400
    assert client.post(path + "/decisions", json={"decisionType": "REJECT_VERSION", "rationale": "Client request"}).json()["data"]["assetVersion"]["status"] == "REJECTED"

def test_idempotent_assets_and_versions(client):
    headers = {"Idempotency-Key": "asset-intent-123"}
    body = {"title": "One asset", "assetType": "OTHER"}
    path = "/api/v1/projects/" + P + "/assets"
    one = client.post(path, json=body, headers=headers)
    two = client.post(path, json=body, headers=headers)
    assert one.status_code == 201 and two.status_code == 200 and one.json() == two.json()
    assert two.headers["Idempotent-Replayed"] == "true"
    assert client.post(path, json={**body, "title": "Different"}, headers=headers).status_code == 422
    aid = one.json()["data"]["id"]
    path = "/api/v1/assets/" + aid + "/versions"
    body = {"metadataOnly": True, "revisionRationale": "Initial version"}
    one = client.post(path, json=body, headers=headers)
    two = client.post(path, json=body, headers=headers)
    assert one.json() == two.json() and two.status_code == 200
    assert client.get("/api/v1/assets/" + aid).json()["data"]["versionCount"] == 1

def test_direction_switch_does_not_rewrite_version(client):
    a = register(client)
    path = "/api/v1/asset-versions/" + a["latestVersion"]["id"]
    before = client.get(path).json()
    client.post("/api/v1/projects/" + P + "/active-direction", json={"directionRevisionId": B, "reason": "Updated client brief"})
    assert client.get(path).json() == before
    assert client.get("/api/v1/projects/" + P + "/assets?directionId=00000000-0000-4000-8000-000000000030").json()["data"][0]["id"] == a["id"]

def test_explicit_null_context_and_external_url(client):
    a = register(client)
    result = client.post("/api/v1/assets/" + a["id"] + "/versions", json={
        "externalFileUrl": "https://example.com/product.webp", "directionRevisionId": None,
        "briefRevisionId": None, "revisionRationale": "External image", "width": 640, "height": 480})
    assert result.status_code == 201
    v = result.json()["data"]
    assert v["directionRevisionId"] is None and v["briefRevisionId"] is None
    assert v["width"] == 640 and not v["isMetadataOnly"]
