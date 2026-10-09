"""Live collection integrity smoke; uses a new project and archives it after checking."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import httpx
BASE = "http://localhost:8000/api/v1"

def main():
    with httpx.Client(base_url=BASE, timeout=20) as client:
        response = client.post("/projects", json={"name": "Collection smoke " + str(uuid4())[:8], "template": "BLANK"})
        response.raise_for_status()
        pid = response.json()["data"]["id"]
        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()["data"]
        try:
            assets = [call("POST", "/projects/" + pid + "/assets", json={"title": "Product " + str(i), "assetType": "PRODUCT_IMAGE",
                "initialVersion": {"metadataOnly": True, "revisionRationale": "Initial version"}}) for i in range(3)]
            c = call("POST", "/projects/" + pid + "/collections", json={"name": "Delivery", "collectionType": "DELIVERABLE",
                "items": [{"assetVersionId": a["latestVersion"]["id"]} for a in assets[:2]]})
            rid = c["latestRevision"]["id"]
            call("POST", "/collection-revisions/" + rid + "/items", json={"assetVersionId": assets[2]["latestVersion"]["id"], "position": 1})
            rows = call("GET", "/collection-revisions/" + rid)["items"]
            call("PATCH", "/collection-revision-items/" + rows[2]["id"], json={"position": 1})
            assert [i["position"] for i in call("GET", "/collection-revisions/" + rid)["items"]] == [1, 2, 3]
            call("POST", "/collection-revisions/" + rid + "/submit-review")
            call("POST", "/collection-revisions/" + rid + "/approve", json={})
            aid = assets[0]["id"]
            v1 = assets[0]["latestVersion"]["id"]
            v2 = call("POST", "/assets/" + aid + "/versions", json={"metadataOnly": True, "derivedFromVersionId": v1, "revisionRationale": "Adapted image"})["id"]
            old = call("GET", "/collection-revisions/" + rid)
            item = next(i for i in old["items"] if i["pinnedVersion"]["assetId"] == aid)
            assert item["pinnedVersion"]["id"] == v1 and item["isStale"]
            blocked = client.patch("/collection-revision-items/" + item["id"], json={"assetVersionId": v2, "rationale": "Explicit replacement"})
            assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "REVISION_FROZEN"
            def new_revision(index):
                with httpx.Client(base_url=BASE, timeout=20) as worker:
                    return worker.post("/collections/" + c["id"] + "/revisions", json={"changeSummary": "Review round " + str(index)})
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(new_revision, range(2)))
            assert sorted(r.status_code for r in responses) == [201, 409]
            draft = next(r.json()["data"] for r in responses if r.status_code == 201)
            item = next(i for i in draft["items"] if i["pinnedVersion"]["assetId"] == aid)
            assert item["pinnedVersion"]["id"] == v1
            call("PATCH", "/collection-revision-items/" + item["id"], json={"assetVersionId": v2, "rationale": "Explicit replacement"})
            call("POST", "/collection-revisions/" + draft["id"] + "/submit-review")
            call("POST", "/collection-revisions/" + draft["id"] + "/approve", json={})
            old = call("GET", "/collection-revisions/" + rid)
            assert old["status"] == "SUPERSEDED"
            assert next(i for i in old["items"] if i["pinnedVersion"]["assetId"] == aid)["pinnedVersion"]["id"] == v1
            assert call("GET", "/collections/" + c["id"])["approvedRevisionId"] == draft["id"]
            print("PostgreSQL collection smoke passed: reorder, frozen pins, stale warning, concurrent revision guard and explicit replacement.")
        finally:
            client.delete("/projects/" + pid, params={"confirm": "true"}).raise_for_status()

if __name__ == "__main__":
    main()
