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
        "MCP_SESSION_STATE_PORT": "9003",
        "MCP_CODE_EXECUTOR_V2_HOST": "backend",
        "PPTX_TEMPLATE_PATH": "/app/templates/deck.pptx",
        "CHATUI_RUNTIME_UPLOADS": "/app/runtime/uploads",
        "CODE_EXECUTOR_V2_WORKSPACES_DIR": "/data/code-executor",
        "CODE_EXECUTOR_V2_MEM_MB": "2048",
        "MCP_TRANSFER_BASE_DIR": "/data/transfers",
        "MCP_TRANSFER_ALLOWED_DIRS": "/data,/tmp",
        "HTTP_PROXY": "http://proxy:3128",
        "HTTPS_PROXY": "http://proxy:3128",
        "http_proxy": "http://proxy:3128",
        "https_proxy": "http://proxy:3128",
        "NO_PROXY": "localhost",
        "no_proxy": "localhost",
        "SSL_CERT_FILE": "/etc/ssl/certs/ca.pem",
        "REQUESTS_CA_BUNDLE": "/etc/ssl/certs/ca.pem",
    }
    for key, value in keys.items():
        monkeypatch.setenv(key, value)
    env = _build_child_env(forward_mcp_config=True)
    assert {key: env.get(key) for key in keys} == keys


def test_mcp_config_keys_are_not_forwarded_to_portal(monkeypatch):
    """The Agent Portal must not inherit credential-bearing proxy/Redis URLs."""
    monkeypatch.setenv("MCP_REDIS_URL", "redis://user:password@redis:6379/0")
    monkeypatch.setenv("HTTP_PROXY", "http://user:password@proxy:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://user:password@proxy:3128")
    monkeypatch.setenv("CODE_EXECUTOR_V2_WORKSPACES_DIR", "/data/code-executor")
    env = _build_child_env()
    assert "MCP_REDIS_URL" not in env
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert "CODE_EXECUTOR_V2_WORKSPACES_DIR" not in env
