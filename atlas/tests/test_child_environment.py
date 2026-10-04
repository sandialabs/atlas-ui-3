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
