"""Live assessment smoke using a dedicated project, archived afterward."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import httpx

BASE = "http://localhost:8000/api/v1"

def main():
    with httpx.Client(base_url=BASE, timeout=20) as client:
        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()["data"]
        p = call("POST", "/projects", json={"name": "Assessment smoke " + str(uuid4())[:8], "template": "DEMO_SOLARA"})
        pid = p["id"]
        try:
            ds = call("GET", "/projects/" + pid + "/directions")
            target = next(d for d in ds if d["name"] == "Warm Organic")["latestRevision"]["id"]
            styles = [
                ("Factory", {"palette": ["graphite"], "materials": ["metal"]}),
                ("Metal", {"palette": ["graphite"], "lighting": ["hard"], "materials": ["metal"], "mood": ["technical"], "composition": ["macro"]}),
                ("Close-up", {"palette": ["graphite"], "lighting": ["hard"], "materials": ["wood"], "mood": ["calm"], "composition": ["close-up"]}),
                ("Hero", {"palette": ["cream"], "lighting": ["soft natural light"], "materials": ["wood"], "mood": ["calm"], "composition": ["hero"]}),
            ]
            assets = [call("POST", "/projects/" + pid + "/assets", json={"title": title, "assetType": "OTHER",
                "initialVersion": {"metadataOnly": True, "recordedAttributes": attributes, "revisionRationale": "Initial version"}}) for title, attributes in styles]
            c = call("POST", "/projects/" + pid + "/collections", json={"name": "Approved originals", "collectionType": "DELIVERABLE",
                "items": [{"assetVersionId": a["latestVersion"]["id"]} for a in assets[:3]]})
            rid = c["latestRevision"]["id"]
            call("POST", "/collection-revisions/" + rid + "/submit-review")
            old_collection = call("POST", "/collection-revisions/" + rid + "/approve", json={})
            body = {"newDirectionRevisionId": target, "mode": "RULES_ONLY", "assetVersionIds": [a["latestVersion"]["id"] for a in assets]}
            key = str(uuid4())
            def create(_):
                with httpx.Client(base_url=BASE, timeout=20) as worker:
                    return worker.post("/projects/" + pid + "/impact-assessments", json=body, headers={"Idempotency-Key": key})
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(create, range(2)))
            assert sorted(r.status_code for r in responses) == [200, 201]
            assert responses[0].json() == responses[1].json()
            a = responses[0].json()["data"]
            assert [i["recommendation"] for i in a["items"]] == ["REVIEW_REQUIRED", "RECREATE_CANDIDATE", "ADAPT_CANDIDATE", "REUSE_CANDIDATE"]
            assert a["counts"]["unresolved"] == 4 and a["imagesSent"] == 0
            item = a["items"][1]
            version_path = "/asset-versions/" + item["assetVersion"]["id"]
            before = call("GET", version_path)
            path = "/impact-assessments/" + a["id"] + "/items/" + item["id"] + "/decision"
            first = call("POST", path, json={"resolution": "OVERRIDE", "overrideRecommendation": "REVIEW_REQUIRED", "rationale": "Human review instead"})
            second = call("POST", path, json={"resolution": "ACCEPT"})
            assert second["decision"]["supersedesDecisionId"] == first["decision"]["id"]
            assert second["item"]["recommendation"] == "RECREATE_CANDIDATE"
            assert call("GET", version_path) == before
            assert call("GET", "/collection-revisions/" + rid) == old_collection
            current = call("GET", "/projects/" + pid + "/decisions", params={"assessmentId": a["id"], "currentOnly": True})
            assert len(current) == 1 and current[0]["id"] == second["decision"]["id"]
            mock = call("POST", "/projects/" + pid + "/impact-assessments", headers={"Idempotency-Key": str(uuid4())},
                json={**body, "mode": "HYBRID", "includeImages": True})
            assert mock["isSimulated"] and mock["providerName"] == "mock" and mock["imagesSent"] == 0
            print("PostgreSQL assessment smoke passed: four classes, concurrent idempotency, override history and unchanged versions/approved pins.")
        finally:
            client.delete("/projects/" + pid, params={"confirm": "true"}).raise_for_status()

if __name__ == "__main__":
    main()
