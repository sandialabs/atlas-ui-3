"""Regression coverage for the shared subprocess environment baseline."""

from atlas.core.child_environment import _build_child_env


def test_portal_extras_still_cannot_forward_secrets(monkeypatch):
    from atlas.modules.process_manager.manager import _build_child_env as portal_env

    assert portal_env is _build_child_env
    monkeypatch.setenv("BACKEND_PRIVATE_VALUE", "test-only-backend-value")
    env = portal_env(
        extra={
            "AWS_ACCESS_KEY_ID": "test-only-value",
            "API_TOKEN": "test-only-value",
            "PYTHONPATH": "/untrusted/modules",
            "SAFE_OPTION": "allowed",
        },
        extra_path_dirs=["/custom/bin", "/custom/bin"],
    )
    assert not {"AWS_ACCESS_KEY_ID", "API_TOKEN", "PYTHONPATH", "BACKEND_PRIVATE_VALUE"}.intersection(env)
    assert env["SAFE_OPTION"] == "allowed"
    assert env["PATH"] == "/custom/bin:/usr/local/bin:/usr/bin:/bin"


def test_bundled_mcp_config_keys_are_forwarded(monkeypatch):
    """Bundled stdio servers need the backend URL, state store, and proxy/CA keys."""
    keys = {
        "CHATUI_BACKEND_BASE_URL": "http://backend:8000",
        "BACKEND_URL": "http://backend:8000",
        "MCP_STATE_BACKEND": "redis",
        "MCP_REDIS_URL": "redis://redis:6379/0",
        "HTTP_PROXY": "http://proxy:3128",
        "HTTPS_PROXY": "http://proxy:3128",
        "NO_PROXY": "localhost",
        "SSL_CERT_FILE": "/etc/ssl/certs/ca.pem",
        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca.pem",
    }
    for key, value in keys.items():
        monkeypatch.setenv(key, value)
    env = _build_child_env()
    assert {key: env.get(key) for key in keys} == keys
