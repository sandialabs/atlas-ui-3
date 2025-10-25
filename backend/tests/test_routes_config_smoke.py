from starlette.testclient import TestClient

from main import app


def test_config_endpoint_smoke(monkeypatch):
    client = TestClient(app)
    resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})
    # Endpoint should not crash; tolerate 200 with minimal fields
    assert resp.status_code == 200
    data = resp.json()
    assert "app_name" in data
    assert "models" in data
    assert "tools" in data
    assert "prompts" in data
    assert "data_sources" in data
