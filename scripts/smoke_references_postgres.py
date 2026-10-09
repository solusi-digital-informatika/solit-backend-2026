"""Live uploaded-file and reference smoke; archives its dedicated project."""
from io import BytesIO
from urllib.parse import urlsplit
from uuid import uuid4
import httpx
from PIL import Image

def main():
    with httpx.Client(base_url="http://localhost:8000/api/v1", timeout=20) as client:
        def call(method, path, **kwargs):
            r = client.request(method, path, **kwargs)
            r.raise_for_status()
            return r.json()
        pid = call("POST", "/projects", json={"name": "Reference smoke " + str(uuid4())[:8], "template": "BLANK"})["data"]["id"]
        try:
            out = BytesIO()
            Image.new("RGB", (32, 24), color="green").save(out, format="PNG")
            content = out.getvalue()
            file = call("POST", "/projects/" + pid + "/uploads", files={"file": ("reference.png", content, "image/png")})["data"]
            url = file["url"]
            response = httpx.get(url)
            assert response.status_code == 200 and response.content == content
            assert "storageKey" not in file
            ref = call("POST", "/projects/" + pid + "/references", json={"title": "Green studio", "sourceType": "USER_UPLOAD",
                "storageObjectId": file["id"], "usageRightsNote": "Synthetic test asset",
                "attributes": [{"attributeType": "PALETTE", "value": {"text": "Green"}}]})["data"]
            a = call("POST", "/projects/" + pid + "/assets", json={"title": "Product visual", "assetType": "PRODUCT_IMAGE",
                "initialVersion": {"storageObjectId": file["id"], "references": [{"referenceId": ref["id"]}], "revisionRationale": "Uploaded version"}})["data"]
            v = call("GET", "/asset-versions/" + a["latestVersion"]["id"])["data"]
            assert not v["isMetadataOnly"] and v["width"] == 32 and v["contentChecksum"] == file["checksum"]
            assert v["references"][0]["referenceId"] == ref["id"]
            simulated = call("POST", "/references/" + ref["id"] + "/analyze", json={})
            assert simulated["meta"]["simulated"]
            inferred = next(x for x in simulated["data"]["attributes"] if x["origin"] == "AI_INFERRED")
            corrected = call("PATCH", "/reference-attributes/" + inferred["id"], json={"value": {"text": "Calm"}})["data"]
            assert corrected["origin"] == "AI_INFERRED" and corrected["reviewStatus"] == "CORRECTED"
            bad = client.post("/projects/" + pid + "/uploads", files={"file": ("bad.png", b"not an image", "image/png")})
            assert bad.status_code == 415
            print("PostgreSQL reference smoke passed: actual upload/download, asset file metadata, reference lineage and simulated attribute correction.")
        finally:
            client.delete("/projects/" + pid, params={"confirm": "true"}).raise_for_status()

if __name__ == "__main__":
    main()
