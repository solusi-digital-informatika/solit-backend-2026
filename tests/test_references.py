from io import BytesIO
from urllib.parse import urlsplit, parse_qs, urlencode
from PIL import Image
from app.modules.references.schemas import ReferenceDTO, StorageDTO

P = "00000000-0000-4000-8000-000000000010"
A = "00000000-0000-4000-8000-000000000031"

def image_bytes(kind="PNG", size=(16, 12)):
    out = BytesIO()
    Image.new("RGB", size, color="green").save(out, format=kind)
    return out.getvalue()

def upload(client, project=P, kind="PNG"):
    response = client.post("/api/v1/projects/" + project + "/uploads", files={"file": ("../../image.exe", image_bytes(kind), "text/plain")})
    assert response.status_code == 201, response.text
    StorageDTO.model_validate(response.json()["data"])
    return response.json()["data"]

def reference(client, file=None, project=P):
    body = {"title": "Botanical", "sourceType": "USER_UPLOAD" if file else "PUBLIC_URL",
        "usageRightsNote": "Internal test use", "tags": ["green"],
        "attributes": [{"attributeType": "PALETTE", "value": {"text": "Green", "hex": "#00FF00"}}]}
    body.update({"storageObjectId": file["id"]} if file else {"sourceUrl": "https://example.com/reference.png"})
    response = client.post("/api/v1/projects/" + project + "/references", json=body)
    assert response.status_code == 201, response.text
    ReferenceDTO.model_validate(response.json()["data"])
    return response.json()["data"]

def test_upload_real_signature_and_signed_download(client):
    for kind in ("PNG", "JPEG", "WEBP"):
        file = upload(client, kind=kind)
        assert file["mimeType"].startswith("image/")
        assert file["width"] == 16 and file["height"] == 12
        assert file["originalFilename"] == "image.exe"
        assert "storageKey" not in file
        parsed = urlsplit(file["url"])
        response = client.get(parsed.path + "?" + parsed.query)
        assert response.status_code == 200 and response.content == image_bytes(kind)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.get(parsed.path + "?expires=1&signature=invalid").status_code == 403

def test_upload_limits_and_fake_image(client):
    url = "/api/v1/projects/" + P + "/uploads"
    assert client.post(url, files={"file": ("fake.png", b"<svg></svg>", "image/png")}).status_code == 415
    assert client.post(url, files={"file": ("big.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")}).status_code == 413
    result = client.post(url, files={"file": ("wide.png", image_bytes(size=(8001, 1)), "image/png")})
    assert result.status_code == 400

def test_reference_attributes_links_and_search(client):
    file = upload(client)
    r = reference(client, file)
    assert r["attributes"][0]["origin"] == "USER_PROVIDED"
    assert r["attributes"][0]["reviewStatus"] == "CONFIRMED"
    path = "/api/v1/references/" + r["id"] + "/links"
    body = {"targetType": "DIRECTION_REVISION", "targetId": A}
    assert client.post(path, json=body).status_code == 201
    assert len(client.post(path, json=body).json()["data"]["links"]) == 1
    assert len(client.get("/api/v1/projects/" + P + "/references?q=green&attributeType=PALETTE").json()["data"]) == 1
    result = client.delete(path, params={"targetType": "DIRECTION_REVISION", "targetId": A})
    assert result.json()["data"]["links"] == []
    assert client.get("/api/v1/projects/" + P + "/references?q=no-match").json()["data"] == []

def test_reference_conflict_and_archive(client):
    r = reference(client)
    path = "/api/v1/references/" + r["id"]
    changed = client.patch(path, json={"archived": True, "expectedUpdatedAt": r["updatedAt"]})
    assert changed.status_code == 200
    assert client.patch(path, json={"title": "Stale", "expectedUpdatedAt": r["updatedAt"]}).status_code == 409
    assert client.get("/api/v1/projects/" + P + "/references").json()["data"] == []
    assert len(client.get("/api/v1/projects/" + P + "/references?includeArchived=true").json()["data"]) == 1

def test_mock_analysis_requires_bytes_and_preserves_origin(client):
    url_reference = reference(client)
    assert client.post("/api/v1/references/" + url_reference["id"] + "/analyze", json={}).json()["error"]["code"] == "NO_IMAGE_INPUT"
    r = reference(client, upload(client))
    response = client.post("/api/v1/references/" + r["id"] + "/analyze", json={})
    assert response.status_code == 200 and response.json()["meta"]["simulated"] is True
    a = next(a for a in response.json()["data"]["attributes"] if a["origin"] == "AI_INFERRED")
    assert a["reviewStatus"] == "UNREVIEWED"
    response = client.patch("/api/v1/reference-attributes/" + a["id"], json={"value": {"text": "Corrected mood"}})
    assert response.json()["data"]["origin"] == "AI_INFERRED" and response.json()["data"]["reviewStatus"] == "CORRECTED"

def test_uploaded_asset_version_metadata_and_references(client):
    file = upload(client)
    r = reference(client, file)
    response = client.post("/api/v1/projects/" + P + "/assets", json={"title": "Uploaded product", "assetType": "PRODUCT_IMAGE",
        "initialVersion": {"storageObjectId": file["id"], "references": [{"referenceId": r["id"], "usageNote": "Palette"}],
            "revisionRationale": "Initial upload", "width": 999, "height": 999}})
    assert response.status_code == 201, response.text
    aid = response.json()["data"]["id"]
    vid = response.json()["data"]["latestVersion"]["id"]
    v = client.get("/api/v1/asset-versions/" + vid).json()["data"]
    assert v["fileUrl"] and not v["isMetadataOnly"]
    assert v["width"] == 16 and v["height"] == 12 and v["contentChecksum"] == file["checksum"]
    assert v["references"][0]["referenceId"] == r["id"]
    path = "/api/v1/references/" + r["id"] + "/links"
    client.post(path, json={"targetType": "ASSET", "targetId": aid})
    assert client.get("/api/v1/assets/" + aid).json()["data"]["referenceLinks"][0]["referenceId"] == r["id"]

def test_cross_project_storage_and_reference(client):
    file = upload(client)
    r = reference(client, file)
    p = client.post("/api/v1/projects", json={"name": "Another", "template": "BLANK"}).json()["data"]["id"]
    bad = client.post("/api/v1/projects/" + p + "/references", json={"title": "Foreign", "sourceType": "USER_UPLOAD", "storageObjectId": file["id"]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "CROSS_PROJECT_REFERENCE"
    bad = client.post("/api/v1/projects/" + p + "/assets", json={"title": "Foreign", "assetType": "OTHER",
        "initialVersion": {"metadataOnly": True, "references": [{"referenceId": r["id"]}], "revisionRationale": "Foreign reference"}})
    assert bad.status_code == 422

def test_invalid_reference_inputs(client):
    url = "/api/v1/projects/" + P + "/references"
    for body in [{"title": "Missing file", "sourceType": "USER_UPLOAD"}, {"title": "Unsafe", "sourceType": "PUBLIC_URL", "sourceUrl": "javascript:alert(1)"},
        {"title": "Invalid URL", "sourceType": "PUBLIC_URL", "sourceUrl": "https://"}, {"title": "Wrong demo", "sourceType": "DEMO_ASSET", "sourceUrl": "/demo-assets/../secret"}]:
        assert client.post(url, json=body).status_code == 400

def test_multipart_body_limit_has_request_id(client):
    response = client.post("/api/v1/projects/" + P + "/uploads",
        files={"file": ("too-large.png", b"x" * (12 * 1024 * 1024), "image/png")},
        headers={"X-Request-Id": "oversized-upload"})
    assert response.status_code == 413
    assert response.json()["error"]["requestId"] == "oversized-upload"
