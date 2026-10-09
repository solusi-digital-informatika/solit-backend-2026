"""Live PostgreSQL smoke test through the running API; uses a new archived test project."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import httpx

BASE = "http://localhost:8000/api/v1"

def main():
    with httpx.Client(base_url=BASE, timeout=20) as client:
        response = client.post("/projects", json={"name": "Smoke " + str(uuid4())[:8], "template": "DEMO_SOLARA"})
        response.raise_for_status()
        p = response.json()["data"]
        pid = p["id"]
        try:
            def save(i):
                with httpx.Client(base_url=BASE, timeout=20) as worker:
                    response = worker.post("/projects/" + pid + "/brief-revisions", json={
                        "title": "Concurrent brief", "objective": "Validate revision allocation", "changeSummary": "Revision " + str(i)})
                    response.raise_for_status()
                    return response.json()["data"]["revisionNumber"]
            with ThreadPoolExecutor(max_workers=4) as pool:
                numbers = sorted(pool.map(save, range(4)))
            assert numbers == [2, 3, 4, 5], numbers
            directions = client.get("/projects/" + pid + "/directions").json()["data"]
            target = next(d for d in directions if not d["isActive"])["latestRevision"]["id"]
            old = p["activeDirectionRevisionId"]
            original = client.get("/direction-revisions/" + old).json()
            switched = client.post("/projects/" + pid + "/active-direction", json={"directionRevisionId": target, "reason": "Smoke change"})
            switched.raise_for_status()
            assert switched.json()["data"]["previousDirectionRevisionId"] == old
            assert client.get("/direction-revisions/" + old).json() == original
            body = {"name": "Idempotent smoke " + str(uuid4())[:8], "template": "BLANK"}
            headers = {"Idempotency-Key": str(uuid4())}
            first = client.post("/projects", json=body, headers=headers)
            first.raise_for_status()
            duplicate_id = first.json()["data"]["id"]
            try:
                replay = client.post("/projects", json=body, headers=headers)
                assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
                assert first.json() == replay.json()
            finally:
                client.delete("/projects/" + duplicate_id, params={"confirm": "true"}).raise_for_status()
            print("PostgreSQL smoke passed: concurrent revisions, immutable direction history, idempotent replay.")
        finally:
            client.delete("/projects/" + pid, params={"confirm": "true"}).raise_for_status()

if __name__ == "__main__":
    main()
