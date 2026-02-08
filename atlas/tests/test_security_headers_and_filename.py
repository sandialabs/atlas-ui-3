from main import app
from starlette.testclient import TestClient


def test_security_headers_present_by_default():
    client = TestClient(app)
    r = client.get("/api/files/healthz", headers={"X-User-Email": "test@test.com"})
    assert r.status_code == 200
    # HSTS intentionally omitted
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") in ("SAMEORIGIN", "DENY")
    assert r.headers.get("Referrer-Policy") is not None
    # CSP may be present per default value
    assert "Content-Security-Policy" in r.headers


def test_csp_includes_websocket_origins_for_configured_port():
    """CSP connect-src should include ws:// and wss:// for the configured port."""
    client = TestClient(app)
    r = client.get("/api/files/healthz", headers={"X-User-Email": "test@test.com"})
    csp = r.headers.get("Content-Security-Policy", "")
    from atlas.modules.config import config_manager
    port = config_manager.app_settings.port
    assert f"ws://localhost:{port}" in csp, f"CSP missing ws://localhost:{port}: {csp}"
    assert f"wss://localhost:{port}" in csp, f"CSP missing wss://localhost:{port}: {csp}"


def test_csp_includes_websocket_origins_for_non_default_port():
    """When port is set to a non-default value, CSP should include that port's WS origins."""
    from atlas.modules.config import config_manager
    original_port = config_manager.app_settings.port
    try:
        config_manager.app_settings.port = 9999
        client = TestClient(app)
        r = client.get("/api/files/healthz", headers={"X-User-Email": "test@test.com"})
        csp = r.headers.get("Content-Security-Policy", "")
        assert "ws://localhost:9999" in csp, f"CSP missing ws://localhost:9999: {csp}"
        assert "wss://localhost:9999" in csp, f"CSP missing wss://localhost:9999: {csp}"
        # Should NOT contain the old port
        if original_port != 9999:
            assert f"ws://localhost:{original_port}" not in csp
    finally:
        config_manager.app_settings.port = original_port


def test_download_filename_sanitized(monkeypatch):
    # Insert a file into mock S3 listing by calling upload
    from atlas.infrastructure.app_factory import app_factory
    app_factory.get_file_manager()

    # Prepare malicious filename
    bad_name = 'evil\r\nInjected.txt'
    content = "SGVsbG8="  # base64(Hello)

    async def upload_stub(user_email, filename, content_base64, content_type, tags, source_type):
        return {
            "key": "k_mal",
            "filename": filename,
            "size": 5,
            "content_type": "text/plain",
            "last_modified": "now",
            "etag": "etag",
            "tags": tags or {},
            "user_email": user_email,
        }

    async def get_stub(user_email, key):
        return {
            "key": key,
            "filename": bad_name,
            "size": 5,
            "content_base64": content,
            "content_type": "text/plain",
            "last_modified": "now",
            "etag": "etag",
            "tags": {},
        }

    # Patch storage client
    s3 = app_factory.get_file_storage()
    monkeypatch.setattr(s3, "upload_file", upload_stub)
    monkeypatch.setattr(s3, "get_file", get_stub)

    client = TestClient(app)

    # Trigger download endpoint directly (no need to actually upload first)
    r = client.get("/api/files/download/k_mal", headers={"X-User-Email": "test@test.com"})
    assert r.status_code == 200
    cd = r.headers.get("Content-Disposition", "")
    assert "\r" not in cd and "\n" not in cd
    assert cd.startswith("attachment;") or cd.startswith("inline;")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
