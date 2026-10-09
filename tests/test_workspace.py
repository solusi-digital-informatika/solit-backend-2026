from concurrent.futures import ThreadPoolExecutor
from app.modules.workspace.schemas import ProjectDTO, BriefDTO, DirectionDTO, RevisionDTO

P = "00000000-0000-4000-8000-000000000010"
A = "00000000-0000-4000-8000-000000000031"
B = "00000000-0000-4000-8000-000000000041"
D = "00000000-0000-4000-8000-000000000030"

def create(client, **kwargs):
    response = client.post("/api/v1/projects", json={"name": "Test project", "template": "BLANK", **kwargs})
    assert response.status_code == 201, response.text
    ProjectDTO.model_validate(response.json()["data"])
    return response.json()["data"]

def test_seed_and_shapes(client):
    project = client.get("/api/v1/projects/" + P).json()["data"]
    ProjectDTO.model_validate(project)
    assert project["activeDirectionRevisionId"] == A
    directions = client.get("/api/v1/projects/" + P + "/directions").json()["data"]
    assert len(directions) == 2
    for direction in directions:
        DirectionDTO.model_validate(direction)
    response = client.get("/api/v1/projects/" + P + "/brief-revisions/latest")
    BriefDTO.model_validate(response.json()["data"])

def test_project_concurrency_archive_restore(client):
    p = create(client)
    response = client.patch("/api/v1/projects/" + p["id"], json={"name": "Changed", "expectedUpdatedAt": p["updatedAt"], "unknown": "ignored"})
    assert response.status_code == 200
    assert response.json()["data"]["name"] == "Changed"
    conflict = client.patch("/api/v1/projects/" + p["id"], json={"name": "Stale", "expectedUpdatedAt": p["updatedAt"]})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CONFLICT"
    assert client.delete("/api/v1/projects/" + p["id"]).status_code == 400
    assert client.delete("/api/v1/projects/" + p["id"] + "?confirm=true").json()["data"]["status"] == "ARCHIVED"
    assert client.post("/api/v1/projects/" + p["id"] + "/restore").json()["data"]["status"] == "ACTIVE"

def test_brief_immutable_and_revision_validation(client):
    p = create(client)
    first = client.post("/api/v1/projects/" + p["id"] + "/brief-revisions", json={"title": "Brief", "objective": "Objective"}).json()["data"]
    url = "/api/v1/projects/" + p["id"] + "/brief-revisions"
    assert client.post(url, json={"title": "Changed", "objective": "Changed"}).status_code == 400
    second = client.post(url, json={"title": "Changed", "objective": "Changed", "changeSummary": "Updated objective"}).json()["data"]
    assert second["revisionNumber"] == 2
    assert client.get("/api/v1/brief-revisions/" + first["id"]).json()["data"] == first
    assert client.get(url + "/latest").json()["data"] == second

def test_direction_switch_and_history(client):
    before = client.get("/api/v1/direction-revisions/" + A).json()["data"]
    result = client.post("/api/v1/projects/" + P + "/active-direction", json={"directionRevisionId": B, "reason": "New client direction"})
    assert result.status_code == 200, result.text
    assert result.json()["data"]["previousDirectionRevisionId"] == A
    assert client.get("/api/v1/direction-revisions/" + A).json()["data"] == before
    d = client.get("/api/v1/directions/" + D).json()["data"]
    assert d["status"] == "SUPERSEDED"
    active = client.get("/api/v1/directions/00000000-0000-4000-8000-000000000040").json()["data"]
    bad = client.patch("/api/v1/directions/" + active["id"], json={"status": "ARCHIVED", "expectedUpdatedAt": active["updatedAt"]})
    assert bad.status_code == 409

def test_new_revision_not_automatically_active(client):
    response = client.post("/api/v1/directions/" + D + "/revisions", json={"summary": "Revised", "changeSummary": "Updated style"})
    assert response.status_code == 201
    RevisionDTO.model_validate(response.json()["data"])
    assert response.json()["data"]["revisionNumber"] == 2
    assert client.get("/api/v1/projects/" + P).json()["data"]["activeDirectionRevisionId"] == A
    assert client.post("/api/v1/directions/" + D + "/revisions", json={"summary": "No reason"}).status_code == 400

def test_clone_and_cross_project(client):
    response = client.post("/api/v1/projects/" + P + "/directions", json={"name": "Alternative", "cloneFromDirectionRevisionId": A})
    assert response.status_code == 201
    assert response.json()["data"]["latestRevision"]["palette"][0]["name"] == "steel blue"
    other = create(client)
    cross = client.post("/api/v1/projects/" + other["id"] + "/directions", json={"name": "Foreign", "cloneFromDirectionRevisionId": A})
    assert cross.status_code == 422
    assert cross.json()["error"]["code"] == "CROSS_PROJECT_REFERENCE"

def test_idempotency(client):
    body = {"name": "One intent", "template": "BLANK"}
    headers = {"Idempotency-Key": "intent-12345"}
    one = client.post("/api/v1/projects", json=body, headers=headers)
    two = client.post("/api/v1/projects", json=body, headers=headers)
    assert one.status_code == 201 and two.status_code == 200
    assert one.json() == two.json()
    assert two.headers["Idempotent-Replayed"] == "true"
    three = client.post("/api/v1/projects", json={**body, "name": "Other"}, headers=headers)
    assert three.status_code == 422

def test_pagination_and_diff(client):
    one = client.get("/api/v1/projects/" + P + "/directions?limit=1").json()
    assert one["page"]["nextCursor"]
    two = client.get("/api/v1/projects/" + P + "/directions", params={"limit": 1, "cursor": one["page"]["nextCursor"]}).json()
    assert one["data"][0]["id"] != two["data"][0]["id"]
    assert two["page"]["nextCursor"] is None
    response = client.get("/api/v1/direction-revisions/diff", params={"from": A, "to": B})
    assert response.status_code == 200
    assert response.json()["data"]["changes"]
    assert client.get("/api/v1/projects?cursor=invalid").status_code == 400

def test_demo_template_uses_new_ids(client):
    p = create(client, template="DEMO_SOLARA")
    assert p["id"] != P
    assert p["activeDirectionRevisionId"] != A
    assert len(client.get("/api/v1/projects/" + p["id"] + "/directions").json()["data"]) == 2

def test_strict_path_and_required_fields(client):
    assert client.get("/api/v1/projects/not-a-uuid").status_code == 400
    assert client.post("/api/v1/projects", json={"name": "No template"}).status_code == 400
    assert client.patch("/api/v1/projects/" + P, json={"name": None, "expectedUpdatedAt": client.get("/api/v1/projects/" + P).json()["data"]["updatedAt"]}).status_code == 400
