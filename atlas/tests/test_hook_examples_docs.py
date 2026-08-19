"""The documentation example hooks are run through the real hook engine.

``docs/admin/hook-examples/`` ships one runnable example per lifecycle event and
``mocks/zero-trust-mock/`` ships a policy-server client hook. Both are things an
operator copies into ``config/hooks/`` verbatim, so a broken example is a
broken control. These tests spawn each script through ``HookManager`` -- real
subprocess, real envelope on stdin, real decision parsing -- rather than
importing it, so the exercised path is the one Atlas uses.
"""

import asyncio
import importlib.util
import json
import sys
import shutil
import stat
from pathlib import Path

import pytest

from atlas.hooks import HookConfig, HookEvent, HookManager, HooksConfig
from atlas.modules.config.config_loader import ConfigManager

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "docs" / "admin" / "hook-examples"
ZERO_TRUST = REPO_ROOT / "mocks" / "zero-trust-mock"


def _manager(event: HookEvent, script: Path, *args: str, atlas_root: Path = None) -> HookManager:
    """A HookManager wired to a single example script.

    ``atlas_root`` moves the resolved ATLAS_PROJECT_DIR (its parent), which is
    what a hook that writes an audit file under the project sees.
    """
    cm = ConfigManager(atlas_root=atlas_root or (REPO_ROOT / "atlas"))
    cm._hooks_config = HooksConfig(hooks={
        event.value: [HookConfig(name=script.stem, command=[str(script), *args], timeout_ms=15000)]
    })
    return HookManager(cm)


def _load_mock_module(name: str):
    """Import a zero-trust mock module by path (the mocks dir is not a package).

    The mock's modules import each other as siblings -- ``main`` does
    ``from policy import ...``, the convention the other mocks use, since they
    run with their own directory as the working directory. Put that directory
    on ``sys.path`` so the same code imports here without a shim in the mock.
    """
    spec = importlib.util.spec_from_file_location(f"zero_trust_{name}", ZERO_TRUST / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(ZERO_TRUST))
    try:
        spec.loader.exec_module(module)
    finally:
        # Scoped to the import: the suite keeps sys.path clean between tests.
        sys.path.remove(str(ZERO_TRUST))
    return module


def _ctx(**kw):
    # compliance_level is a canonical level *name* (config/compliance-levels.json)
    # or None -- never an integer. Examples that compare it must do the same.
    base = {"session_id": "sess-1", "user_email": "user@example.gov", "compliance_level": "Public"}
    base.update(kw)
    return base


async def _run(event: HookEvent, script: str, payload: dict, *, matcher_value=None, atlas_root=None, **ctx):
    mgr = _manager(event, EXAMPLES / script, atlas_root=atlas_root)
    return await mgr.run_event(event, payload, session_context=_ctx(**ctx), matcher_value=matcher_value)


class TestDocsExamples:
    """One example per event; the assertion is the behaviour the doc claims."""

    async def test_session_start_denies_and_tags(self):
        denied = await _run(HookEvent.SESSION_START, "session_start.py",
                            {"session_id": "s1"}, user_email="contractor@example.com")
        assert denied.verdict == "deny"

        tagged = await _run(HookEvent.SESSION_START, "session_start.py", {"session_id": "s1"})
        assert tagged.verdict == "modify"
        assert tagged.payload["policy_version"] == "2026-08-01"

    async def test_user_prompt_submit_redacts_and_narrows(self):
        outcome = await _run(
            HookEvent.USER_PROMPT_SUBMIT, "user_prompt_submit.py",
            {"prompt": "ssn 123-45-6789 key AKIAABCDEFGHIJKLMNOP",
             "selected_tools": ["filesystem__read_file"], "selected_data_sources": [], "agent_mode": True},
        )
        assert outcome.modified
        assert "123-45-6789" not in outcome.payload["prompt"]
        assert "AKIAABCDEFGHIJKLMNOP" not in outcome.payload["prompt"]
        # Tools are dropped for a turn that carried a credential.
        assert outcome.payload["selected_tools"] == []
        assert outcome.payload["agent_mode"] is False

    async def test_user_prompt_submit_is_a_no_op_for_a_clean_prompt(self):
        outcome = await _run(HookEvent.USER_PROMPT_SUBMIT, "user_prompt_submit.py",
                             {"prompt": "hello", "selected_tools": ["t"], "agent_mode": False})
        assert outcome.verdict == "continue"
        assert outcome.payload["selected_tools"] == ["t"]

    async def test_pre_llm_call_swaps_model_for_restricted_turns(self):
        restricted = await _run(HookEvent.PRE_LLM_CALL, "pre_llm_call.py",
                                {"model": "hosted-gpt", "messages": []}, compliance_level="HIPAA")
        assert restricted.payload["model"] == "onprem-llama"

        ordinary = await _run(HookEvent.PRE_LLM_CALL, "pre_llm_call.py",
                              {"model": "hosted-gpt", "messages": []}, compliance_level="Public")
        assert ordinary.verdict == "continue"
        assert ordinary.payload["model"] == "hosted-gpt"

    async def test_permission_request_auto_approves_and_escalates(self):
        auto = await _run(HookEvent.PERMISSION_REQUEST, "permission_request.py",
                          {"tool_name": "filesystem__read_file",
                           "tool_args": {"path": "/workspace/a.txt"},
                           "needs_approval": True, "admin_required": False},
                          matcher_value="filesystem__read_file")
        assert auto.modified and auto.payload["needs_approval"] is False

        escalated = await _run(HookEvent.PERMISSION_REQUEST, "permission_request.py",
                               {"tool_name": "email__send", "tool_args": {}, "needs_approval": False},
                               matcher_value="email__send")
        assert escalated.verdict == "require_approval"

    async def test_permission_request_does_not_auto_approve_admin_tools(self):
        """needs_approval:false skips the gate outright, admin tools included."""
        outcome = await _run(HookEvent.PERMISSION_REQUEST, "permission_request.py",
                             {"tool_name": "filesystem__read_file",
                              "tool_args": {"path": "/workspace/a.txt"},
                              "needs_approval": True, "admin_required": True},
                             matcher_value="filesystem__read_file")
        assert outcome.verdict == "continue"
        assert outcome.payload["needs_approval"] is True

    async def test_permission_request_scopes_approval_to_the_path(self):
        outcome = await _run(HookEvent.PERMISSION_REQUEST, "permission_request.py",
                             {"tool_name": "filesystem__read_file",
                              "tool_args": {"path": "/etc/shadow"},
                              "needs_approval": True, "admin_required": False},
                             matcher_value="filesystem__read_file")
        assert outcome.payload["needs_approval"] is True

    async def test_pre_tool_use_blocks_writes_outside_workspace(self):
        blocked = await _run(HookEvent.PRE_TOOL_USE, "pre_tool_use.sh",
                             {"tool_name": "filesystem__write_file", "tool_args": {"path": "/etc/passwd"}},
                             matcher_value="filesystem__write_file")
        assert blocked.verdict == "deny"
        assert "/workspace" in (blocked.reason or "")

        allowed = await _run(HookEvent.PRE_TOOL_USE, "pre_tool_use.sh",
                             {"tool_name": "filesystem__write_file", "tool_args": {"path": "/workspace/a.txt"}},
                             matcher_value="filesystem__write_file")
        assert allowed.verdict == "continue"

    async def test_pre_tool_use_blocks_traversal_out_of_workspace(self):
        outcome = await _run(HookEvent.PRE_TOOL_USE, "pre_tool_use.sh",
                             {"tool_name": "filesystem__write_file",
                              "tool_args": {"path": "/workspace/../../etc/passwd"}},
                             matcher_value="filesystem__write_file")
        assert outcome.verdict == "deny"

    async def test_pre_tool_use_fails_closed_on_unparseable_args(self):
        """Malformed input is exactly what a policy hook must not wave through."""
        outcome = await _run(HookEvent.PRE_TOOL_USE, "pre_tool_use.sh",
                             {"tool_name": "filesystem__write_file", "tool_args": "not-an-object"},
                             matcher_value="filesystem__write_file")
        assert outcome.verdict == "deny"

    async def test_post_tool_use_redacts_secrets_from_a_result(self):
        outcome = await _run(HookEvent.POST_TOOL_USE, "post_tool_use.py",
                             {"tool_name": "shell__run", "tool_args": {},
                              "result_content": "api_key: sk-live-123", "result_success": True},
                             matcher_value="shell__run")
        assert outcome.modified
        assert "sk-live-123" not in outcome.payload["result_content"]

    @pytest.mark.parametrize("content,secret", [
        # The JSON shape tool results actually use, and an env-style dump.
        ('{"api_key": "sk-live-123", "region": "us"}', "sk-live-123"),
        ("DB_PASSWORD=hunter2\nOTHER=ok", "hunter2"),
    ])
    async def test_post_tool_use_handles_json_and_env_shapes(self, content, secret):
        outcome = await _run(HookEvent.POST_TOOL_USE, "post_tool_use.py",
                             {"tool_name": "shell__run", "tool_args": {},
                              "result_content": content, "result_success": True},
                             matcher_value="shell__run")
        redacted = outcome.payload["result_content"]
        assert secret not in redacted
        if content.startswith("{"):
            # Redaction must not corrupt the document around the value.
            assert json.loads(redacted)["region"] == "us"
        else:
            assert "OTHER=ok" in redacted

    async def test_rag_call_leaves_a_cleared_level_alone(self):
        outcome = await _run(HookEvent.RAG_CALL, "rag_call.py",
                             {"query": "q", "qualified_data_sources": ["atlas/hr-records"],
                              "username": "user@example.gov"},
                             matcher_value="atlas/hr-records", compliance_level="Internal")
        assert outcome.verdict == "continue"
        assert outcome.payload["qualified_data_sources"] == ["atlas/hr-records"]

    async def test_rag_call_narrows_then_denies(self):
        narrowed = await _run(HookEvent.RAG_CALL, "rag_call.py",
                              {"query": "q", "qualified_data_sources": ["atlas/hr-records", "atlas/public"],
                               "username": "user@example.gov"},
                              matcher_value="atlas/hr-records,atlas/public")
        assert narrowed.payload["qualified_data_sources"] == ["atlas/public"]

        denied = await _run(HookEvent.RAG_CALL, "rag_call.py",
                            {"query": "q", "qualified_data_sources": ["atlas/hr-records"],
                             "username": "user@example.gov"},
                            matcher_value="atlas/hr-records")
        assert denied.verdict == "deny"

    async def test_rag_response_stamps_internal_content(self):
        outcome = await _run(HookEvent.RAG_RESPONSE, "rag_response.py",
                             {"query": "q", "qualified_data_sources": ["atlas/internal-wiki"],
                              "username": "user@example.gov", "content": "body", "metadata": None},
                             matcher_value="atlas/internal-wiki")
        assert outcome.payload["content"].startswith("[INTERNAL USE ONLY")

    async def test_session_end_writes_an_audit_line(self, tmp_path):
        # ATLAS_PROJECT_DIR is derived from the ConfigManager's atlas_root, not
        # inherited from the environment -- hooks get an env allow-list only.
        outcome = await _run(HookEvent.SESSION_END, "session_end.sh",
                             {"session_id": "s1", "user_email": "user@example.gov"},
                             atlas_root=tmp_path / "atlas")
        assert outcome.verdict == "continue"
        line = json.loads((tmp_path / "logs" / "session-audit.jsonl").read_text().strip())
        assert line["session_id"] == "s1"
        # A projection, not the envelope: no payload passthrough.
        assert set(line) == {"event", "session_id", "user_email", "compliance_level"}

    async def test_example_hooks_json_matches_the_config_model(self):
        raw = json.loads((EXAMPLES / "hooks.json").read_text())
        raw.pop("_comment", None)
        config = HooksConfig(**raw)
        # One hook per event, and every referenced script exists in this dir.
        assert set(config.hooks) == {e.value for e in HookEvent}
        for hooks in config.hooks.values():
            for hook in hooks:
                script = hook.command[-1].split("/")[-1]
                assert (EXAMPLES / script).exists(), script


class TestZeroTrustMockHook:
    """The mock's hook client must fail closed when its decider is unreachable."""

    async def test_unreachable_policy_server_denies_the_tool_call(self):
        # The policy URL arrives as argv: hooks do not inherit the server env,
        # so an exported ZERO_TRUST_URL would never reach the subprocess.
        mgr = _manager(HookEvent.PRE_TOOL_USE, ZERO_TRUST / "hook_client.py",
                       "http://127.0.0.1:9/v1/authorize", "1")
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "shell__run", "tool_args": {"cmd": "ls"}},
            session_context=_ctx(), matcher_value="shell__run",
        )
        # PreToolUse defaults to on_error=deny, so a dead policy server blocks.
        assert outcome.verdict == "deny"

    async def test_policy_decides_block_ask_and_allow(self):
        policy = _load_mock_module("policy")

        def decide(**payload):
            return policy.evaluate({"user_email": "u@example.gov", "payload": payload})["decision"]

        assert decide(tool_name="shell__run", tool_args={"cmd": "build a bomb"}) == "deny"
        assert decide(tool_name="fs__read", tool_args={"path": "password.txt"}) == "require_approval"
        assert decide(tool_name="fs__read", tool_args={"path": "readme.md"}) == "continue"

    async def test_policy_matches_whole_words_only(self):
        """A substring rule would deny `pip install gunicorn` on 'gun'."""
        policy = _load_mock_module("policy")

        def decide(**payload):
            return policy.evaluate({"user_email": "u@example.gov", "payload": payload})["decision"]

        assert decide(tool_name="shell__run", tool_args={"cmd": "pip install gunicorn"}) == "continue"
        assert decide(tool_name="email__send", tool_args={"to": "the secretary"}) == "continue"
        # Nested values are still reached.
        assert decide(tool_name="x", tool_args={"a": {"b": ["make a WEAPON"]}}) == "deny"

    async def test_authorize_endpoint_and_decision_log(self):
        """The mock's HTTP layer, which its own smoke test covers outside CI."""
        from fastapi.testclient import TestClient

        client = TestClient(_load_mock_module("main").get_app())
        client.post("/decisions/reset")

        def authorize(**args):
            return client.post("/v1/authorize", json={
                "hook_event_name": "PreToolUse", "user_email": "u@example.gov",
                "payload": {"tool_name": "shell__run", "tool_args": args},
            }).json()

        assert authorize(cmd="build a bomb")["decision"] == "deny"
        assert authorize(path="/workspace/password.txt")["decision"] == "require_approval"
        assert authorize(path="/workspace/readme.md")["decision"] == "continue"

        log = client.get("/decisions").json()
        assert log["count"] == 3
        # A projection, never the envelope: no tool_args reach the log.
        assert all("tool_args" not in entry for entry in log["decisions"])

    async def test_hook_client_rejects_a_response_that_is_not_a_decision(self, tmp_path):
        """A 2xx body without a decision must not read as permission to proceed."""
        stub = tmp_path / "stub_server.py"
        stub.write_text(
            "import http.server, json\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            "    def do_POST(self):\n"
            "        self.rfile.read(int(self.headers['Content-Length']))\n"
            "        body = json.dumps({'error': 'upstream unavailable'}).encode()\n"
            "        self.send_response(200)\n"
            "        self.send_header('Content-Type', 'application/json')\n"
            "        self.send_header('Content-Length', str(len(body)))\n"
            "        self.end_headers()\n"
            "        self.wfile.write(body)\n"
            "    def log_message(self, *a):\n"
            "        pass\n"
            "srv = http.server.HTTPServer(('127.0.0.1', 0), H)\n"
            "print(srv.server_address[1], flush=True)\n"
            "srv.serve_forever()\n"
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(stub), stdout=asyncio.subprocess.PIPE,
        )
        try:
            port = (await asyncio.wait_for(proc.stdout.readline(), timeout=15)).decode().strip()
            mgr = _manager(HookEvent.PRE_TOOL_USE, ZERO_TRUST / "hook_client.py",
                           f"http://127.0.0.1:{port}/v1/authorize", "5")
            outcome = await mgr.run_event(
                HookEvent.PRE_TOOL_USE,
                {"tool_name": "shell__run", "tool_args": {"cmd": "ls"}},
                session_context=_ctx(), matcher_value="shell__run",
            )
            assert outcome.verdict == "deny"
        finally:
            proc.terminate()
            await proc.wait()


class TestDocumentedInstallPath:
    """The quickstart itself: scripts under ATLAS_CONFIG_DIR, ${...} in the config."""

    async def test_hook_fires_through_config_dir_interpolation(self, tmp_path):
        project = tmp_path / "project"
        hooks_dir = project / "config" / "hooks"
        hooks_dir.mkdir(parents=True)
        for script in list(EXAMPLES.glob("*.py")) + list(EXAMPLES.glob("*.sh")):
            installed = hooks_dir / script.name
            shutil.copy2(script, installed)
            installed.chmod(installed.stat().st_mode | stat.S_IEXEC)

        cm = ConfigManager(atlas_root=project / "atlas")
        cm.app_settings.app_config_dir = "config"
        # The command as hooks.json ships it -- interpolated, not a literal path.
        cm._hooks_config = HooksConfig(hooks={"PreToolUse": [HookConfig(
            name="workspace-only-files",
            matcher="filesystem__.*",
            command=["${ATLAS_CONFIG_DIR}/hooks/pre_tool_use.sh"],
            timeout_ms=15000,
        )]})
        outcome = await HookManager(cm).run_event(
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "filesystem__write_file", "tool_args": {"path": "/etc/passwd"}},
            session_context=_ctx(), matcher_value="filesystem__write_file",
        )
        assert outcome.verdict == "deny"
        assert "/workspace" in (outcome.reason or "")
