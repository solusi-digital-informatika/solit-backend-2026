from app.modules.collections.schemas import CollectionDetail, RevisionDTO

P = "00000000-0000-4000-8000-000000000010"

def asset(client, project=P):
    response = client.post("/api/v1/projects/" + project + "/assets", json={"title": "Product", "assetType": "PRODUCT_IMAGE",
        "initialVersion": {"metadataOnly": True, "revisionRationale": "Initial version"}})
    assert response.status_code == 201
    return response.json()["data"]

def collection(client, version_ids=(), project=P):
    response = client.post("/api/v1/projects/" + project + "/collections", json={"name": "Deliverable", "collectionType": "DELIVERABLE",
        "items": [{"assetVersionId": vid} for vid in version_ids]})
    assert response.status_code == 201, response.text
    CollectionDetail.model_validate(response.json()["data"])
    return response.json()["data"]

def approve(client, rid):
    assert client.post("/api/v1/collection-revisions/" + rid + "/submit-review").status_code == 200
    response = client.post("/api/v1/collection-revisions/" + rid + "/approve", json={})
    assert response.status_code == 200
    RevisionDTO.model_validate(response.json()["data"])
    return response.json()["data"]

def test_pin_preserved_and_explicit_revision(client):
    a = asset(client)
    v1 = a["latestVersion"]["id"]
    c = collection(client, [v1])
    rid = c["latestRevision"]["id"]
    approved = approve(client, rid)
    v2 = client.post("/api/v1/assets/" + a["id"] + "/versions", json={"metadataOnly": True, "derivedFromVersionId": v1,
        "revisionRationale": "Revised version"}).json()["data"]["id"]
    original = client.get("/api/v1/collection-revisions/" + rid).json()["data"]
    item = original["items"][0]
    assert item["pinnedVersion"]["id"] == v1 and item["latestVersion"]["id"] == v2
    assert item["isStale"] and item["newerVersionCount"] == 1
    assert client.patch("/api/v1/collection-revision-items/" + item["id"], json={"assetVersionId": v2, "rationale": "New version"}).status_code == 409
    assert client.delete("/api/v1/collection-revision-items/" + item["id"]).json()["error"]["code"] == "REVISION_FROZEN"
    draft = client.post("/api/v1/collections/" + c["id"] + "/revisions", json={"changeSummary": "Update approved set"}).json()["data"]
    assert draft["items"][0]["pinnedVersion"]["id"] == v1
    draft_item = draft["items"][0]["id"]
    assert client.patch("/api/v1/collection-revision-items/" + draft_item, json={"assetVersionId": v2}).status_code == 400
    changed = client.patch("/api/v1/collection-revision-items/" + draft_item, json={"assetVersionId": v2, "rationale": "Use revised image"})
    assert changed.status_code == 200 and not changed.json()["data"]["isStale"]
    approve(client, draft["id"])
    assert client.get("/api/v1/collection-revisions/" + rid).json()["data"]["status"] == "SUPERSEDED"
    assert client.get("/api/v1/collection-revisions/" + rid).json()["data"]["items"][0]["pinnedVersion"]["id"] == v1
    assert client.get("/api/v1/collections/" + c["id"]).json()["data"]["approvedRevisionId"] == draft["id"]
    pinned = client.get("/api/v1/asset-versions/" + v1).json()["data"]["pinnedIn"]
    assert pinned[0]["collectionRevisionId"] == rid

def test_duplicate_and_atomic_create(client):
    a = asset(client)
    vid = a["latestVersion"]["id"]
    response = client.post("/api/v1/projects/" + P + "/collections", json={"name": "Duplicate", "collectionType": "OTHER",
        "items": [{"assetVersionId": vid}, {"assetVersionId": vid}]})
    assert response.status_code == 409 and response.json()["error"]["code"] == "ASSET_ALREADY_PINNED"
    assert client.get("/api/v1/projects/" + P + "/collections").json()["data"] == []
    c = collection(client, [vid])
    rid = c["latestRevision"]["id"]
    assert client.post("/api/v1/collection-revisions/" + rid + "/items", json={"assetVersionId": vid}).status_code == 409

def test_reorder_insert_delete_gapless(client):
    a, b, d = asset(client), asset(client), asset(client)
    c = collection(client, [a["latestVersion"]["id"], b["latestVersion"]["id"]])
    rid = c["latestRevision"]["id"]
    response = client.post("/api/v1/collection-revisions/" + rid + "/items", json={"assetVersionId": d["latestVersion"]["id"], "position": 1})
    assert response.status_code == 201, response.text
    rows = client.get("/api/v1/collection-revisions/" + rid).json()["data"]["items"]
    assert [i["position"] for i in rows] == [1, 2, 3]
    assert client.patch("/api/v1/collection-revision-items/" + rows[2]["id"], json={"position": 1}).status_code == 200
    rows = client.get("/api/v1/collection-revisions/" + rid).json()["data"]["items"]
    assert rows[0]["pinnedVersion"]["assetId"] == b["id"]
    response = client.delete("/api/v1/collection-revision-items/" + rows[1]["id"])
    assert [i["position"] for i in response.json()["data"]["items"]] == [1, 2]

def test_review_state_and_open_revision(client):
    c = collection(client)
    rid = c["latestRevision"]["id"]
    assert client.post("/api/v1/collection-revisions/" + rid + "/submit-review").status_code == 400
    assert client.post("/api/v1/collection-revisions/" + rid + "/approve", json={}).status_code == 409
    assert client.post("/api/v1/collections/" + c["id"] + "/revisions", json={"changeSummary": "Duplicate draft"}).json()["error"]["code"] == "OPEN_REVISION_EXISTS"
    a = asset(client)
    client.post("/api/v1/collection-revisions/" + rid + "/items", json={"assetVersionId": a["latestVersion"]["id"]})
    client.post("/api/v1/collection-revisions/" + rid + "/submit-review")
    frozen = client.post("/api/v1/collection-revisions/" + rid + "/items", json={"assetVersionId": a["latestVersion"]["id"]})
    assert frozen.json()["error"]["code"] == "REVISION_FROZEN"
    rejected = client.post("/api/v1/collection-revisions/" + rid + "/reject", json={"decision": "CHANGES_REQUESTED", "comment": "Change lighting"})
    assert rejected.json()["data"]["status"] == "REJECTED"
    assert rejected.json()["data"]["approvals"][0]["decision"] == "CHANGES_REQUESTED"
    assert client.post("/api/v1/collections/" + c["id"] + "/revisions", json={"changeSummary": "Address feedback"}).status_code == 201

def test_foreign_project_and_wrong_asset_replacement(client):
    a, b = asset(client), asset(client)
    c = collection(client, [a["latestVersion"]["id"]])
    rid = c["latestRevision"]["id"]
    item = client.get("/api/v1/collection-revisions/" + rid).json()["data"]["items"][0]
    assert client.patch("/api/v1/collection-revision-items/" + item["id"], json={"assetVersionId": b["latestVersion"]["id"], "rationale": "Different asset"}).status_code == 400
    p = client.post("/api/v1/projects", json={"name": "Other project", "template": "BLANK"}).json()["data"]["id"]
    foreign = asset(client, p)
    response = client.post("/api/v1/collection-revisions/" + rid + "/items", json={"assetVersionId": foreign["latestVersion"]["id"]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "CROSS_PROJECT_REFERENCE"

def test_collection_edit_concurrency_and_archive(client):
    c = collection(client)
    url = "/api/v1/collections/" + c["id"]
    changed = client.patch(url, json={"name": "Changed", "expectedUpdatedAt": c["updatedAt"]})
    assert changed.status_code == 200
    assert client.patch(url, json={"name": "Stale", "expectedUpdatedAt": c["updatedAt"]}).status_code == 409
    archived = client.patch(url, json={"archived": True, "expectedUpdatedAt": changed.json()["data"]["updatedAt"]})
    assert archived.json()["data"]["status"] == "ARCHIVED"
    assert client.get("/api/v1/projects/" + P + "/collections").json()["data"] == []
    restored = client.patch(url, json={"archived": False, "expectedUpdatedAt": archived.json()["data"]["updatedAt"]})
    assert restored.json()["data"]["status"] == "DRAFT"

def test_collection_and_revision_idempotency(client):
    a = asset(client)
    body = {"name": "Idempotent", "collectionType": "OTHER", "items": [{"assetVersionId": a["latestVersion"]["id"]}]}
    headers = {"Idempotency-Key": "collection-intent-123"}
    path = "/api/v1/projects/" + P + "/collections"
    one = client.post(path, json=body, headers=headers)
    two = client.post(path, json=body, headers=headers)
    assert one.status_code == 201 and two.status_code == 200 and one.json() == two.json()
    assert client.post(path, json={**body, "name": "Different"}, headers=headers).status_code == 422
    c = one.json()["data"]
    approve(client, c["latestRevision"]["id"])
    path = "/api/v1/collections/" + c["id"] + "/revisions"
    body = {"changeSummary": "New review round"}
    one = client.post(path, json=body, headers=headers)
    two = client.post(path, json=body, headers=headers)
    assert one.status_code == 201 and two.status_code == 200 and one.json() == two.json()

def test_revision_list_pagination(client):
    a = asset(client)
    c = collection(client, [a["latestVersion"]["id"]])
    approve(client, c["latestRevision"]["id"])
    client.post("/api/v1/collections/" + c["id"] + "/revisions", json={"changeSummary": "Next round"})
    path = "/api/v1/collections/" + c["id"] + "/revisions"
    first = client.get(path + "?limit=1").json()
    second = client.get(path, params={"limit": 1, "cursor": first["page"]["nextCursor"]}).json()
    assert first["data"][0]["revisionNumber"] == 2
    assert second["data"][0]["revisionNumber"] == 1

def test_pin_decision_visible_in_asset_detail(client):
    a = asset(client)
    c = collection(client, [a["latestVersion"]["id"]])
    decisions = client.get("/api/v1/assets/" + a["id"]).json()["data"]["recentDecisions"]
    assert decisions[0]["decisionType"] == "PIN_TO_COLLECTION"
    assert decisions[0]["collectionRevisionId"] == c["latestRevision"]["id"]
