"""Real HTTP adapter smoke against a local fake router; never uses paid provider credentials."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import uuid4
from io import BytesIO
import json
from PIL import Image
from fastapi.testclient import TestClient
from app.main import app, settings
from app.modules.assessments.rules import assess

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.path == "/v1/chat/completions"
        assert self.headers["Authorization"] == "Bearer local-fake-key"
        assert body["model"] == "local-fake-model"
        content = body["messages"][-1]["content"]
        if isinstance(content, str):
            result = "OK"
        elif content[0]["text"].startswith("{"):
            context = json.loads(content[0]["text"])
            items = assess(context, "AI_ASSISTED")
            images = [i for i in content if i["type"] == "image_url"]
            assert len(images) == len(context["imageVersionIds"]) == 4
            result = json.dumps({"items": items})
        else:
            assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
            result = json.dumps({"attributes": [{"attributeType": "PALETTE", "value": {"text": "Hijau", "hex": "#008000"}}]})
        payload = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": result}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = {key: getattr(settings, key) for key in ["ai_mode", "ai_api_key", "ai_base_url", "ai_model", "ai_provider_name", "ai_image_analysis_enabled"]}
    settings.ai_mode = "openai"
    settings.ai_api_key = "local-fake-key"
    settings.ai_base_url = "http://127.0.0.1:" + str(server.server_port) + "/v1"
    settings.ai_model = "local-fake-model"
    settings.ai_provider_name = "local-fake-router"
    settings.ai_image_analysis_enabled = True
    try:
        with TestClient(app) as client:
            def call(method, path, **kwargs):
                response = client.request(method, "/api/v1" + path, **kwargs)
                assert response.is_success, response.text
                return response.json()["data"]
            p = call("POST", "/projects", json={"name": "Router local smoke " + str(uuid4())[:8], "template": "DEMO_SOLARA"})
            pid = p["id"]
            try:
                directions = call("GET", "/projects/" + pid + "/directions")
                b = next(d["latestRevision"]["id"] for d in directions if d["name"] == "Warm Organic")
                a = call("POST", "/projects/" + pid + "/impact-assessments",
                    headers={"Idempotency-Key": str(uuid4())},
                    json={"newDirectionRevisionId": b, "mode": "AI_ASSISTED", "includeImages": True})
                assert a["status"] == "COMPLETED" and not a["isSimulated"] and a["imagesSent"] == 4
                assert a["providerName"] == "local-fake-router"
                buf = BytesIO()
                Image.new("RGB", (16, 12), "green").save(buf, "PNG")
                uploaded = call("POST", "/projects/" + pid + "/uploads", files={"file": ("green.png", buf.getvalue(), "image/png")})
                r = call("POST", "/projects/" + pid + "/references", json={
                    "title": "Router reference", "sourceType": "USER_UPLOAD", "storageObjectId": uploaded["id"]})
                response = client.post("/api/v1/references/" + r["id"] + "/analyze")
                assert response.status_code == 200 and response.json()["meta"]["simulated"] is False
                assert response.json()["data"]["attributes"][0]["reviewStatus"] == "UNREVIEWED"
                assert call("POST", "/settings/ai/test")["ok"]
                print("Local HTTP router + PostgreSQL smoke passed: real adapter, image bytes, provider metadata, reference attributes and connectivity. No paid provider called.")
            finally:
                client.delete("/api/v1/projects/" + pid, params={"confirm": "true"}).raise_for_status()
    finally:
        for key, value in original.items():
            setattr(settings, key, value)
        server.shutdown()
        server.server_close()

if __name__ == "__main__":
    main()
