"""End-to-end integration tests for the Agent Portal.

These walk the real FastAPI router (launch -> list -> get -> cancel)
and the ``atlas-portal`` CLI through a TestClient-backed server. The
goal is to catch regressions in the full happy path — the seams
between routes, process manager, env builder, and preset store — that
unit tests miss by construction.

Test doubles:
- ``app_factory.get_config_manager`` is monkey-patched to return a
  stub with the feature flag flipped on and a unique test user.
- ``log_sanitizer.get_current_user`` is overridden via FastAPI's
  dependency-override mechanism so we do not need the real auth
  middleware.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.modules.agent_portal.presets_store import PresetStore


class _Settings:
    feature_agent_portal_enabled = True
    debug_mode = True
    feature_proxy_secret_enabled = False
    proxy_secret = ""
    proxy_secret_header = "x-proxy-secret"
    auth_user_header = "x-forwarded-user"
    test_user = "e2e@test.com"


class _CM:
    app_settings = _Settings()


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Spin up a FastAPI app wrapping the real agent-portal router.

    Uses a tmpdir-backed preset store and a fresh process manager per
    test so state cannot leak between tests.
    """
    from atlas.core import log_sanitizer as log_san
    from atlas.modules.agent_portal import presets_store as ps_mod
    from atlas.modules.process_manager import manager as pm_mod
    from atlas.routes import agent_portal_routes as ap_routes

    # Fresh state per test
    ps_mod._singleton = PresetStore(path=tmp_path / "presets.json")
    pm_mod._singleton_manager = None  # lazily rebuilt by get_process_manager

    monkeypatch.setattr(ap_routes.app_factory, "get_config_manager", lambda: _CM())

    async def _fake_user():
        return "e2e@test.com"

    app = FastAPI()
    app.include_router(ap_routes.router)
    app.dependency_overrides[log_san.get_current_user] = _fake_user

    with TestClient(app) as client:
        yield client

    ps_mod._singleton = None
    pm_mod._singleton_manager = None


def _wait_for_exit(client: TestClient, process_id: str, timeout_s: float = 5.0) -> dict:
    import time

    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        res = client.get(f"/api/agent-portal/processes/{process_id}")
        assert res.status_code == 200, res.text
        last = res.json()
        if last["status"] != "running":
            return last
        time.sleep(0.05)
    raise AssertionError(f"process {process_id} did not exit within {timeout_s}s; last={last}")


# ---------------------------------------------------------------------------
# Core happy-path flow
# ---------------------------------------------------------------------------


def test_launch_list_get_cancel_round_trip(app_client: TestClient):
    # 1. empty list on fresh state
    r = app_client.get("/api/agent-portal/processes")
    assert r.status_code == 200
    assert r.json()["processes"] == []

    # 2. launch a short-lived command
    r = app_client.post(
        "/api/agent-portal/processes",
        json={"command": "sh", "args": ["-c", "echo hello-e2e"]},
    )
    assert r.status_code == 201, r.text
    summary = r.json()
    assert summary["user_email"] == "e2e@test.com"
    assert summary["status"] in ("running", "exited")
    pid = summary["id"]

    # 3. list includes the launched process
    r = app_client.get("/api/agent-portal/processes")
    assert r.status_code == 200
    ids = [p["id"] for p in r.json()["processes"]]
    assert pid in ids

    # 4. get returns the same id and either still-running or exited
    r = app_client.get(f"/api/agent-portal/processes/{pid}")
    assert r.status_code == 200
    assert r.json()["id"] == pid

    # 5. wait for exit and sanity-check the exit_code
    final = _wait_for_exit(app_client, pid)
    assert final["status"] == "exited"
    assert final["exit_code"] == 0


def test_launch_unknown_command_reports_clear_error(app_client: TestClient):
    r = app_client.post(
        "/api/agent-portal/processes",
        json={"command": "definitely-not-a-binary-xyzzy"},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    # The FileNotFoundError message should mention the command and hint
    # at the server PATH resolution behavior.
    assert "definitely-not-a-binary-xyzzy" in detail
    assert "server PATH" in detail


def test_cancel_running_process(app_client: TestClient):
    # Launch something that will hang until killed.
    r = app_client.post(
        "/api/agent-portal/processes",
        json={"command": "sh", "args": ["-c", "sleep 30"]},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = app_client.delete(f"/api/agent-portal/processes/{pid}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("cancelled", "exited", "failed")

    final = _wait_for_exit(app_client, pid)
    # cancel() sets status synchronously; the final state after wait is
    # either cancelled (common) or exited (child handled SIGTERM fast).
    assert final["status"] in ("cancelled", "exited", "failed")


def test_rename_process(app_client: TestClient):
    r = app_client.post(
        "/api/agent-portal/processes",
        json={"command": "sh", "args": ["-c", "echo x"]},
    )
    pid = r.json()["id"]
    r2 = app_client.patch(
        f"/api/agent-portal/processes/{pid}",
        json={"display_name": "my-test-job"},
    )
    assert r2.status_code == 200
    assert r2.json()["display_name"] == "my-test-job"


# ---------------------------------------------------------------------------
# Env isolation + bare-command resolution end-to-end
# ---------------------------------------------------------------------------


def test_env_isolation_end_to_end(app_client: TestClient, monkeypatch: pytest.MonkeyPatch):
    """A launched child must not see the backend's secrets.

    We seed the parent env with a fake secret, launch ``sh -c 'env'``,
    and assert the secret is absent from the child's output.
    """
    monkeypatch.setenv("FAKE_API_KEY", "this-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "likewise-this-must-not-leak")

    r = app_client.post(
        "/api/agent-portal/processes",
        json={"command": "sh", "args": ["-c", "env"]},
    )
    pid = r.json()["id"]
    final = _wait_for_exit(app_client, pid)
    assert final["status"] == "exited"

    # Find the ManagedProcess to pull its history.
    from atlas.modules.process_manager import get_process_manager
    managed = get_process_manager().get(pid)
    stdout = "\n".join(c.text for c in managed.history if c.stream == "stdout")
    assert "this-must-not-leak" not in stdout
    assert "likewise-this-must-not-leak" not in stdout
    # PATH is pinned; should be the hardcoded value
    assert "PATH=/usr/local/bin:/usr/bin:/bin" in stdout


def test_bare_command_resolves_via_server_path_end_to_end(
    app_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A launch that asks for a bare command installed off the child's
    pinned PATH must still work because the parent resolves via
    shutil.which() against the server's own PATH."""
    custom_bin = tmp_path / "custom_bin"
    custom_bin.mkdir()
    script = custom_bin / "e2e-script-xyz"
    script.write_text("#!/bin/sh\necho resolved-e2e\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{custom_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}")

    r = app_client.post(
        "/api/agent-portal/processes",
        json={"command": "e2e-script-xyz"},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    final = _wait_for_exit(app_client, pid)
    assert final["status"] == "exited"
    assert final["exit_code"] == 0

    from atlas.modules.process_manager import get_process_manager
    managed = get_process_manager().get(pid)
    stdout = [c.text for c in managed.history if c.stream == "stdout"]
    assert "resolved-e2e" in stdout


# ---------------------------------------------------------------------------
# Preset <-> launch integration
# ---------------------------------------------------------------------------


def test_preset_round_trip_and_launch(app_client: TestClient):
    # Create
    r = app_client.post(
        "/api/agent-portal/presets",
        json={
            "name": "echo-hello",
            "description": "e2e",
            "command": "sh",
            "args": ["-c", "echo preset-ran"],
        },
    )
    assert r.status_code == 201
    preset = r.json()
    # List
    r = app_client.get("/api/agent-portal/presets")
    assert any(p["id"] == preset["id"] for p in r.json()["presets"])
    # Launch "from preset" (simulate the UI: fetch preset, POST the fields)
    r = app_client.post(
        "/api/agent-portal/processes",
        json={"command": preset["command"], "args": preset["args"]},
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    final = _wait_for_exit(app_client, pid)
    assert final["status"] == "exited"


def test_feature_flag_off_hides_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When the flag is off every route must 404, even with a valid user."""
    from atlas.core import log_sanitizer as log_san
    from atlas.modules.agent_portal import presets_store as ps_mod
    from atlas.routes import agent_portal_routes as ap_routes

    class _Off:
        feature_agent_portal_enabled = False
        debug_mode = True
        feature_proxy_secret_enabled = False
        proxy_secret = ""
        proxy_secret_header = "x-proxy-secret"
        auth_user_header = "x-forwarded-user"
        test_user = "e2e@test.com"

    class _CM2:
        app_settings = _Off()

    monkeypatch.setattr(ap_routes.app_factory, "get_config_manager", lambda: _CM2())
    ps_mod._singleton = PresetStore(path=tmp_path / "presets.json")

    app = FastAPI()
    app.include_router(ap_routes.router)

    async def _fake_user():
        return "e2e@test.com"

    app.dependency_overrides[log_san.get_current_user] = _fake_user
    with TestClient(app) as client:
        for path in (
            "/api/agent-portal/capabilities",
            "/api/agent-portal/processes",
            "/api/agent-portal/presets",
        ):
            r = client.get(path)
            assert r.status_code == 404, f"{path} should 404 when disabled; got {r.status_code}"


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_cli_help_loads():
    """The CLI parser must at least build without raising."""
    from atlas.portal_cli import build_parser

    parser = build_parser()
    # Parsing --help would call sys.exit; instead parse a real command.
    ns = parser.parse_args(["list"])
    assert ns.subcommand == "list"


def test_cli_launch_via_stubbed_request(monkeypatch: pytest.MonkeyPatch, capsys):
    """End-to-end path through cmd_launch with _request stubbed out.

    This checks body construction, sandbox/pty/cwd wiring, and that the
    CLI prints a reasonable summary. It does not need a real server.
    """
    from atlas import portal_cli

    captured = {}

    def _fake_request(method, url, *, user, auth_header, body=None):
        captured["method"] = method
        captured["url"] = url
        captured["user"] = user
        captured["body"] = body
        return 201, {
            "id": "abc123",
            "status": "running",
            "pid": 42,
            "sandboxed": body.get("sandbox_mode", "off") != "off",
            "sandbox_mode": body.get("sandbox_mode", "off"),
        }

    monkeypatch.setattr(portal_cli, "_request", _fake_request)

    # argparse REMAINDER on command_args means flags must precede the
    # positional `command`. This mirrors shell invocation:
    #   atlas-portal launch --cwd /tmp --sandbox workspace-write sh -- -c "echo hi"
    rc = portal_cli.main(
        [
            "launch",
            "--cwd",
            "/tmp",
            "--sandbox",
            "workspace-write",
            "sh",
            "--",
            "-c",
            "echo hi",
        ]
    )
    assert rc == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/api/agent-portal/processes")
    assert captured["body"]["command"] == "sh"
    assert captured["body"]["args"] == ["-c", "echo hi"]
    assert captured["body"]["cwd"] == "/tmp"
    assert captured["body"]["sandbox_mode"] == "workspace-write"

    out = capsys.readouterr().out
    assert "launched: abc123" in out


def test_cli_list_prints_summary(monkeypatch: pytest.MonkeyPatch, capsys):
    from atlas import portal_cli

    def _fake_request(method, url, *, user, auth_header, body=None):
        return 200, {
            "processes": [
                {
                    "id": "deadbeef-0000",
                    "status": "exited",
                    "pid": 101,
                    "exit_code": 0,
                    "command": "sh",
                    "args": ["-c", "echo hi"],
                    "display_name": "hi",
                }
            ]
        }

    monkeypatch.setattr(portal_cli, "_request", _fake_request)
    rc = portal_cli.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "deadbeef" in out
    assert "exited" in out
    assert "hi" in out
