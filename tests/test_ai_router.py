
import json
from uuid import uuid4
import httpx
import pytest
import app.main as main
from app.modules.assessments.rules import assess
from tests.test_assessments import assets, start, P, A, B
from tests.test_references import upload, reference

@pytest.fixture
def routed(client, monkeypatch):
    for key, value in {"ai_mode": "openai", "ai_base_url": "https://router.example/api/v1",
        "ai_api_key": "secret-router-key", "ai_model": "google/test-gemini",
        "ai_provider_name": "custom-router", "ai_image_analysis_enabled": False,
        "ai_json_mode": True, "ai_max_output_tokens": 6000, "ai_timeout_seconds": 60}.items():
        monkeypatch.setattr(main.settings, key, value)
    return client

def response(url, output, finish="stop"):
    return httpx.Response(200, json={"choices": [{"finish_reason": finish,
        "message": {"content": json.dumps(output)}}]}, request=httpx.Request("POST", url))

def fake_analysis(monkeypatch, mutate=None, calls=None):
    def post(url, **kwargs):
        body = kwargs["json"]
        assert url == "https://router.example/api/v1/chat/completions"
        assert body["model"] == "google/test-gemini" and body["max_tokens"] == 6000
        assert body["response_format"] == {"type": "json_object"}
        assert kwargs["headers"]["Authorization"] == "Bearer secret-router-key"
        assert "secret-router-key" not in json.dumps(body)
        context = json.loads(body["messages"][1]["content"][0]["text"])
        if calls is not None:
            calls.append(context)
        output = assess(context, "AI_ASSISTED")
        if mutate:
            mutate(output, context, body)
        return response(url, {"items": output})
    monkeypatch.setattr(httpx, "post", post)

def test_real_assessment_and_idempotency_call_once(routed, monkeypatch):
    assets(routed)
    calls = []
    fake_analysis(monkeypatch, calls=calls)
    key = str(uuid4())
    first = start(routed, mode="AI_ASSISTED", key=key)
    assert first.status_code == 201, first.text
    data = first.json()["data"]
    assert data["status"] == "COMPLETED" and not data["isSimulated"]
    assert data["providerName"] == "custom-router" and data["modelName"] == "google/test-gemini"
    assert data["promptTemplateVersion"] == "branchframe-router-v1"
    assert data["imagesSent"] == 0 and all(i["analysisSource"] == "AI" for i in data["items"])
    assert calls[0]["oldDirection"]["summary"] and calls[0]["imageVersionIds"] == []
    replay = start(routed, mode="AI_ASSISTED", key=key)
    assert replay.status_code == 200 and len(calls) == 1 and replay.json() == first.json()

def test_hybrid_rules_classification_preserved(routed, monkeypatch):
    assets(routed)
    def change(items, context, body):
        assert context["rulesBaseline"]
        for item in items:
            item["recommendation"] = "REUSE_CANDIDATE"
            item["rationale"] = "AI explanation"
    fake_analysis(monkeypatch, mutate=change)
    data = start(routed, mode="HYBRID").json()["data"]
    assert data["status"] == "COMPLETED"
    assert [i["recommendation"] for i in data["items"]] == [
        "REVIEW_REQUIRED", "RECREATE_CANDIDATE", "ADAPT_CANDIDATE", "REUSE_CANDIDATE"]
    assert all(i["analysisSource"] == "HYBRID" and "AI explanation" in i["rationale"] for i in data["items"])

@pytest.mark.parametrize("fault", ["foreign_id", "image_claim", "missing_item", "empty_evidence", "wrong_enum"])
def test_real_invalid_output_never_persists_items(routed, monkeypatch, fault):
    assets(routed)
    def change(items, context, body):
        if fault == "foreign_id":
            items[0]["assetVersionId"] = str(uuid4())
        elif fault == "image_claim":
            items[0]["supportingEvidence"] = [{"source": "IMAGE_OBSERVATION", "statement": "Imagined image",
                "sourceId": items[0]["assetVersionId"], "ruleId": None}]
        elif fault == "missing_item":
            items.pop()
        elif fault == "empty_evidence":
            items[0]["supportingEvidence"], items[0]["conflictingEvidence"] = [], []
        else:
            items[0]["recommendation"] = "INVALID"
    fake_analysis(monkeypatch, mutate=change)
    data = start(routed, mode="AI_ASSISTED").json()["data"]
    assert data["status"] == "FAILED" and data["errorCode"] == "PROVIDER_ERROR" and data["items"] is None
    assert not data["isSimulated"] and "secret-router-key" not in json.dumps(data)

@pytest.mark.parametrize("failure,code", [("timeout", "PROVIDER_TIMEOUT"), ("http", "PROVIDER_ERROR"),
    ("truncated", "PROVIDER_ERROR"), ("json", "PROVIDER_ERROR")])
def test_real_errors_persist_safely_and_retry_snapshot(routed, monkeypatch, failure, code):
    created = assets(routed)
    def post(url, **kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-router-key")
        if failure == "http":
            return httpx.Response(401, json={"error": "secret-router-key"}, request=httpx.Request("POST", url))
        if failure == "truncated":
            return response(url, {"items": []}, finish="length")
        return httpx.Response(200, json={"choices": [{"message": {"content": "not JSON secret-router-key"}}]},
            request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    data = start(routed, mode="AI_ASSISTED").json()["data"]
    assert data["status"] == "FAILED" and data["errorCode"] == code and "secret-router-key" not in json.dumps(data)
    routed.post("/api/v1/assets/" + created[0]["id"] + "/versions",
        json={"metadataOnly": True, "revisionRationale": "New version after failure"})
    calls = []
    fake_analysis(monkeypatch, calls=calls)
    retry = routed.post("/api/v1/impact-assessments/" + data["id"] + "/retry").json()["data"]
    assert retry["status"] == "COMPLETED" and retry["id"] == data["id"]
    assert {v["id"] for v in calls[0]["versions"]} == {a["latestVersion"]["id"] for a in created}

def test_rules_and_mock_never_call_router(routed, monkeypatch):
    assets(routed)
    def post(*args, **kwargs):
        raise AssertionError("Unexpected paid request")
    monkeypatch.setattr(httpx, "post", post)
    assert start(routed, mode="RULES_ONLY").json()["data"]["status"] == "COMPLETED"
    monkeypatch.setattr(main.settings, "ai_mode", "mock")
    assert start(routed, mode="AI_ASSISTED").json()["data"]["isSimulated"]

def test_image_assessment_true_evidence_and_count(routed, monkeypatch):
    monkeypatch.setattr(main.settings, "ai_image_analysis_enabled", True)
    uploaded = upload(routed)
    a = routed.post("/api/v1/projects/" + P + "/assets", json={
        "title": "Actual upload", "assetType": "OTHER", "initialVersion": {
            "storageObjectId": uploaded["id"], "revisionRationale": "Analyze uploaded version"}}).json()["data"]
    def mutate(items, context, body):
        assert context["imageVersionIds"] == [a["latestVersion"]["id"]]
        image = next(x for x in body["messages"][1]["content"] if x["type"] == "image_url")
        assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
        items[0]["supportingEvidence"] = [{"source": "IMAGE_OBSERVATION", "statement": "Green visible image",
            "sourceId": items[0]["assetVersionId"], "ruleId": None}]
    fake_analysis(monkeypatch, mutate=mutate)
    data = start(routed, mode="AI_ASSISTED", includeImages=True).json()["data"]
    assert data["status"] == "COMPLETED" and data["imagesSent"] == 1

def test_reference_vision_attributes_unreviewed_and_no_url_fetch(routed, monkeypatch):
    monkeypatch.setattr(main.settings, "ai_image_analysis_enabled", True)
    r = reference(routed, upload(routed))
    def post(url, **kwargs):
        messages = kwargs["json"]["messages"]
        assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return response(url, {"attributes": [{"attributeType": "PALETTE", "value": {"text": "Hijau", "hex": "#008000"}}]})
    monkeypatch.setattr(httpx, "post", post)
    result = routed.post("/api/v1/references/" + r["id"] + "/analyze")
    assert result.status_code == 200, result.text
    assert result.json()["meta"]["simulated"] is False
    a = result.json()["data"]["attributes"][-1]
    assert a["origin"] == "AI_INFERRED" and a["reviewStatus"] == "UNREVIEWED"
    assert a["value"]["hex"] == "#008000"
    public = reference(routed)
    assert routed.post("/api/v1/references/" + public["id"] + "/analyze").json()["error"]["code"] == "NO_IMAGE_INPUT"

@pytest.mark.parametrize("output", [{"attributes": []}, {"attributes": [{"attributeType": "PALETTE", "value": {"text": "Green", "hex": "bad"}}]}])
def test_reference_invalid_output_no_partial_writes(routed, monkeypatch, output):
    monkeypatch.setattr(main.settings, "ai_image_analysis_enabled", True)
    r = reference(routed, upload(routed))
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: response(url, output))
    result = routed.post("/api/v1/references/" + r["id"] + "/analyze")
    assert result.status_code == 502 and result.json()["error"]["code"] == "PROVIDER_ERROR"
    after = routed.get("/api/v1/references/" + r["id"]).json()["data"]
    assert after["attributes"] == r["attributes"]

def test_json_mode_optional_and_vision_disabled(routed, monkeypatch):
    monkeypatch.setattr(main.settings, "ai_json_mode", False)
    def post(url, **kwargs):
        assert "response_format" not in kwargs["json"]
        return response(url, {"attributes": []})
    monkeypatch.setattr(httpx, "post", post)
    from app.core.ai_router import complete_json
    assert complete_json([{"role": "user", "content": "JSON please"}]) == {"attributes": []}
    r = reference(routed, upload(routed))
    assert routed.post("/api/v1/references/" + r["id"] + "/analyze").json()["error"]["code"] == "AI_NOT_CONFIGURED"
    assert not routed.get("/api/v1/settings/ai").json()["data"]["imageAnalysisAvailable"]

@pytest.mark.parametrize("url", ["https://[broken", "https://example.com:invalid/v1", "https://user:password@example.com/v1"])
def test_bad_configuration_returns_safe_error(routed, monkeypatch, url):
    monkeypatch.setattr(main.settings, "ai_base_url", url)
    result = routed.post("/api/v1/settings/ai/test")
    assert result.status_code == 422 and result.json()["error"]["code"] == "AI_NOT_CONFIGURED"
    assert "password" not in result.text

@pytest.mark.parametrize("content,ok", [("```json\n{\"items\": []}\n```", True), ("```", False)])
def test_fenced_json_safe_parser(routed, monkeypatch, content, ok):
    def post(url, **kwargs):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]},
            request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    from app.core.ai_router import complete_json
    from app.core.errors import DomainError
    if ok:
        assert complete_json([{"role": "user", "content": "JSON"}]) == {"items": []}
    else:
        with pytest.raises(DomainError) as error:
            complete_json([{"role": "user", "content": "JSON"}])
        assert error.value.code == "PROVIDER_ERROR"

def test_retry_respects_disabled_vision_without_network(routed, monkeypatch):
    from app.modules.assessments.provider import analyze
    from app.core.errors import DomainError
    def post(*args, **kwargs):
        raise AssertionError("Unexpected network request")
    monkeypatch.setattr(httpx, "post", post)
    snapshot = {"versions": [{"id": str(uuid4())}], "images": [{"assetVersionId": str(uuid4()), "dataUrl": "data:image/jpeg;base64,AA"}]}
    with pytest.raises(DomainError) as error:
        analyze(snapshot, "AI_ASSISTED")
    assert error.value.code == "AI_NOT_CONFIGURED"
    monkeypatch.setattr(main.settings, "ai_max_assets_per_assessment", 0)
    with pytest.raises(DomainError) as error:
        analyze(snapshot, "AI_ASSISTED")
    assert error.value.code == "ASSESSMENT_SCOPE_TOO_LARGE"
