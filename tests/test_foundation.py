from fastapi.testclient import TestClient
from app.main import app

def test_contract_health():
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok", "apiVersion": "1.0.0",
        "contractVersion": "1.0.0", "aiMode": "mock", "demoMode": True, "authMode": "demo"}}
    assert response.headers["X-Request-Id"]

def test_error_envelope():
    with TestClient(app) as client:
        response = client.get("/api/v1/missing", headers={"X-Request-Id": "test-request"})
    assert response.status_code == 404
    assert response.json()["error"] == {"code": "NOT_FOUND", "message": "Resource not found.",
                                       "requestId": "test-request", "details": []}

def test_cors():
    with TestClient(app) as client:
        response = client.options("/api/v1/health", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-allow-credentials"] == "true"
