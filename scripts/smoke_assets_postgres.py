"""Live PostgreSQL asset tests. Creates a dedicated project and archives it afterward."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import httpx

BASE = "http://localhost:8000/api/v1"

def main():
    with httpx.Client(base_url=BASE, timeout=20) as client:
        response = client.post("/projects", json={"name": "Asset smoke " + str(uuid4())[:8], "template": "BLANK"})
        response.raise_for_status()
        pid = response.json()["data"]["id"]
        try:
            response = client.post("/projects/" + pid + "/assets", json={"title": "Concurrency test", "assetType": "PRODUCT_IMAGE",
                "initialVersion": {"metadataOnly": True, "revisionRationale": "Initial version", "prompt": "Original prompt"}})
            response.raise_for_status()
            a = response.json()["data"]
            aid, first_id = a["id"], a["latestVersion"]["id"]
            approved = client.post("/asset-versions/" + first_id + "/decisions", json={"decisionType": "APPROVE_VERSION", "rationale": "Approve original"})
            approved.raise_for_status()
            def save(i):
                with httpx.Client(base_url=BASE, timeout=20) as worker:
                    result = worker.post("/assets/" + aid + "/versions", json={
                        "metadataOnly": True, "derivedFromVersionId": first_id, "revisionRationale": "Concurrent version " + str(i)})
                    result.raise_for_status()
                    return result.json()["data"]["versionNumber"]
            with ThreadPoolExecutor(max_workers=4) as pool:
                numbers = sorted(pool.map(save, range(4)))
            assert numbers == [2, 3, 4, 5], numbers
            old = client.get("/asset-versions/" + first_id).json()["data"]
            assert old["status"] == "APPROVED" and old["prompt"] == "Original prompt"
            assert len(old["lineage"]["derivedVersions"]) == 4
            body = {"metadataOnly": True, "revisionRationale": "Idempotent final version"}
            headers = {"Idempotency-Key": str(uuid4())}
            one = client.post("/assets/" + aid + "/versions", json=body, headers=headers)
            one.raise_for_status()
            two = client.post("/assets/" + aid + "/versions", json=body, headers=headers)
            assert two.status_code == 200 and two.headers["Idempotent-Replayed"] == "true"
            assert one.json() == two.json()
            assert client.get("/assets/" + aid).json()["data"]["versionCount"] == 6
            print("PostgreSQL asset smoke passed: concurrent numbering, preserved approval/prompt, lineage and idempotent replay.")
        finally:
            client.delete("/projects/" + pid, params={"confirm": "true"}).raise_for_status()

if __name__ == "__main__":
    main()
