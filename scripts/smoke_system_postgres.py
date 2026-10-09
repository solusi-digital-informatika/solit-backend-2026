"""Exercise the complete demo workflow in a dedicated clone, archived afterward."""
import csv
import json
from io import StringIO
from uuid import uuid4
import httpx

BASE = "http://localhost:8000/api/v1"

def main():
    with httpx.Client(base_url=BASE, timeout=30) as client:
        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()["data"]
        p = call("POST", "/projects", json={"name": "System smoke " + str(uuid4())[:8], "template": "DEMO_SOLARA"})
        pid = p["id"]
        try:
            summary = call("GET", "/projects/" + pid + "/summary")
            assert summary["assetCounts"]["total"] == 4 and summary["staleCollectionItemCount"] == 0
            directions = call("GET", "/projects/" + pid + "/directions")
            a = next(d["latestRevision"]["id"] for d in directions if d["name"] == "Cold Industrial")
            b = next(d["latestRevision"]["id"] for d in directions if d["name"] == "Warm Organic")
            assets = call("GET", "/projects/" + pid + "/assets")
            close = next(x for x in assets if x["title"] == "Product Close-up")
            c = call("GET", "/projects/" + pid + "/collections")[0]
            rid = c["approvedRevisionId"]
            original = call("GET", "/collection-revisions/" + rid)
            call("POST", "/projects/" + pid + "/active-direction",
                json={"directionRevisionId": b, "reason": "Smoke test direction change"})
            body = {"oldDirectionRevisionId": a, "newDirectionRevisionId": b, "mode": "RULES_ONLY"}
            key = str(uuid4())
            assessment = call("POST", "/projects/" + pid + "/impact-assessments", json=body, headers={"Idempotency-Key": key})
            replay = client.post("/projects/" + pid + "/impact-assessments", json=body, headers={"Idempotency-Key": key})
            assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
            assert [i["recommendation"] for i in assessment["items"]] == [
                "REVIEW_REQUIRED", "RECREATE_CANDIDATE", "ADAPT_CANDIDATE", "REUSE_CANDIDATE"]
            item = assessment["items"][1]
            override = call("POST", "/impact-assessments/" + assessment["id"] + "/items/" + item["id"] + "/decision",
                json={"resolution": "OVERRIDE", "overrideRecommendation": "REVIEW_REQUIRED", "rationale": "Confirm material fit first"})
            assert override["item"]["recommendation"] == "RECREATE_CANDIDATE"
            assert override["item"]["effectiveRecommendation"] == "REVIEW_REQUIRED"
            assert call("GET", "/projects/" + pid + "/summary")["unresolvedRecommendationCount"] == 3
            v = call("POST", "/assets/" + close["id"] + "/versions", json={
                "derivedFromVersionId": close["latestVersion"]["id"], "metadataOnly": True,
                "revisionRationale": "Adapt palette and lighting"})
            stale = call("GET", "/collection-revisions/" + rid)
            close_item = next(i for i in stale["items"] if i["pinnedVersion"]["assetId"] == close["id"])
            assert close_item["pinnedVersion"]["id"] == close["latestVersion"]["id"] and close_item["isStale"]
            assert call("GET", "/projects/" + pid + "/summary")["staleCollectionItemCount"] == 1
            patch = {"assetVersionId": v["id"], "rationale": "Explicitly replace approved pin"}
            frozen = client.patch("/collection-revision-items/" + close_item["id"], json=patch)
            assert frozen.status_code == 409 and frozen.json()["error"]["code"] == "REVISION_FROZEN"
            r2 = call("POST", "/collections/" + c["id"] + "/revisions", json={"changeSummary": "Pin adapted version"})
            replacement = next(i for i in r2["items"] if i["pinnedVersion"]["assetId"] == close["id"])
            call("PATCH", "/collection-revision-items/" + replacement["id"], json=patch)
            call("POST", "/collection-revisions/" + r2["id"] + "/submit-review")
            call("POST", "/collection-revisions/" + r2["id"] + "/approve", json={})
            assert call("GET", "/projects/" + pid + "/summary")["staleCollectionItemCount"] == 0
            old = call("GET", "/collection-revisions/" + rid)
            assert old["status"] == "SUPERSEDED"
            assert [i["pinnedVersion"]["id"] for i in old["items"]] == [i["pinnedVersion"]["id"] for i in original["items"]]
            for format in ["JSON", "MARKDOWN", "CSV"]:
                exported = call("POST", "/projects/" + pid + "/exports", json={"format": format})
                assert "signature=" not in exported["content"]
                if format == "JSON":
                    record = json.loads(exported["content"])
                    assert len(record["assessments"][0]["items"]) == 4 and len(record["collections"][0]["revisions"]) == 2
                elif format == "MARKDOWN":
                    assert "## Collections and exact version pins" in exported["content"]
                else:
                    assert len(list(csv.DictReader(StringIO(exported["content"])))) == 4
            assert call("GET", "/settings/ai")["mode"] == "mock"
            assert call("POST", "/settings/ai/test")["ok"]
            client.get("http://localhost:8000/demo-assets/hero-v2.jpg").raise_for_status()
            print("PostgreSQL system smoke passed: complete seed, summary, assessment/override, immutable approved pins, revised approval, all exports and mock AI test.")
        finally:
            client.delete("/projects/" + pid, params={"confirm": "true"}).raise_for_status()

if __name__ == "__main__":
    main()
