"""Unit tests for the core hook engine (GH #713).

Covers config models, the subprocess contract (stdin envelope, exit codes,
stdout JSON), composition rules (deny short-circuits, require_approval sticky,
modify chains), the security model (trusted fields re-asserted, env
allow-list, no shell), timeout/crash/malformed-output handling, and the
zero-overhead no-config path.

Hooks here are real tiny executables written into a tmp dir so we exercise the
actual asyncio subprocess path. They are language-agnostic by construction
(scripts use the host's python) -- this is the contract operators rely on.
"""

import json
import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from atlas.hooks import (
    HookBlockedError,
    HookConfig,
    HookEvent,
    HookManager,
    HooksConfig,
    default_on_error,
    set_hook_manager_for_testing,
)
from atlas.modules.config.config_loader import ConfigManager

# ----------------------------------------------------------- helpers/fixtures


def _write_hook(tmp_path: Path, name: str, body: str) -> str:
    """Write a python hook script and return its absolute path."""
    p = tmp_path / f"{name}.py"
    p.write_text(textwrap.dedent(body))
    p.chmod(0o755)
    return str(p)


def _make_manager(tmp_path: Path, hooks: dict) -> HookManager:
    """Build a HookManager backed by a ConfigManager whose hooks_config returns
    the given ``{event_name: [HookConfig, ...]}`` mapping."""
    cm = ConfigManager(atlas_root=Path(__file__).resolve().parents[1])
    cm._hooks_config = HooksConfig(hooks=hooks)
    return HookManager(cm)


def _session_ctx(**kw):
    base = {
        "session_id": "sess-123",
        "user_email": "user@example.gov",
        "compliance_level": 3,
    }
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def _reset_singleton():
    yield
    set_hook_manager_for_testing(None)


# ----------------------------------------------------------------- models


class TestHookConfig:
    def test_matches_wildcard(self):
        h = HookConfig(name="a", command=["true"])
        assert h.matches("anything")
        assert h.matches(None)
        assert h.matches("")

    def test_matches_regex(self):
        h = HookConfig(name="a", command=["true"], matcher="filesystem__.*")
        assert h.matches("filesystem__write_file")
        assert not h.matches("calc__add")
        assert not h.matches(None)

    def test_matches_star_literal(self):
        h = HookConfig(name="a", command=["true"], matcher="*")
        assert h.matches("anything")
        assert h.matches(None)

    def test_invalid_regex_is_rejected_at_config_load(self):
        # A malformed matcher must fail validation rather than silently never
        # matching: on a fail-closed event that would quietly delete a control.
        with pytest.raises(ValidationError, match="not a valid regex"):
            HookConfig(name="a", command=["true"], matcher="(unclosed")

    def test_invalid_regex_built_directly_fires_rather_than_skips(self):
        # Bypassing validation (model_construct) should still not silently
        # disable the hook -- an uncompilable pattern errs toward firing.
        h = HookConfig.model_construct(name="a", command=["true"], matcher="(unclosed")
        assert h.matches("x") is True

    def test_matcher_on_matcherless_event_warns_at_load(self, caplog):
        # SessionStart supplies no matcher value, so this hook can never fire.
        # The operator must learn that at load time, not by noticing their policy
        # silently never ran.
        with caplog.at_level("WARNING"):
            HooksConfig(hooks={"SessionStart": [
                HookConfig(name="never-fires", command=["true"], matcher="something"),
            ]})
        assert any("will never fire" in r.getMessage() for r in caplog.records)

    def test_wildcard_matcher_on_matcherless_event_does_not_warn(self, caplog):
        with caplog.at_level("WARNING"):
            HooksConfig(hooks={"SessionStart": [
                HookConfig(name="fine", command=["true"], matcher="*"),
            ]})
        assert not [r for r in caplog.records if "will never fire" in r.getMessage()]

    def test_effective_on_error_uses_event_default(self):
        h = HookConfig(name="a", command=["true"])
        assert h.effective_on_error(HookEvent.PRE_TOOL_USE) == "deny"
        assert h.effective_on_error(HookEvent.POST_TOOL_USE) == "allow"

    def test_effective_on_error_explicit_overrides(self):
        h = HookConfig(name="a", command=["true"], on_error="allow")
        assert h.effective_on_error(HookEvent.PRE_TOOL_USE) == "allow"

    def test_command_must_be_non_empty(self):
        with pytest.raises(Exception):
            HookConfig(name="a", command=[])

    def test_name_must_be_non_empty(self):
        with pytest.raises(Exception):
            HookConfig(name=" ", command=["true"])


class TestDefaults:
    def test_security_events_fail_closed(self):
        for e in (HookEvent.PRE_TOOL_USE, HookEvent.PERMISSION_REQUEST, HookEvent.PRE_LLM_CALL, HookEvent.RAG_CALL):
            assert default_on_error(e) == "deny", f"{e} should default deny"

    def test_observability_events_fail_open(self):
        for e in (HookEvent.SESSION_START, HookEvent.SESSION_END, HookEvent.USER_PROMPT_SUBMIT, HookEvent.POST_TOOL_USE, HookEvent.RAG_RESPONSE):
            assert default_on_error(e) == "allow", f"{e} should default allow"


# -------------------------------------------------------------- no-op path


class TestZeroOverhead:
    async def test_no_config_returns_continue_not_fired(self):
        mgr = _make_manager(Path("/tmp"), hooks={})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "x", "tool_args": {}},
            session_context=_session_ctx(),
            matcher_value="x",
        )
        assert outcome.verdict == "continue"
        assert outcome.fired is False
        assert outcome.hook_names == []

    async def test_has_hooks_false_when_empty(self):
        mgr = _make_manager(Path("/tmp"), hooks={})
        assert mgr.has_hooks(HookEvent.PRE_TOOL_USE) is False

    async def test_has_hooks_true_when_registered(self):
        mgr = _make_manager(Path("/tmp"), hooks={"PreToolUse": [HookConfig(name="a", command=["true"])]})
        assert mgr.has_hooks(HookEvent.PRE_TOOL_USE) is True

    async def test_config_load_error_treated_as_empty(self):
        cm = MagicMock()
        cm.hooks_config = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
        # property on a MagicMock is awkward; instead make the attribute raise
        cm2 = MagicMock()
        type(cm2).hooks_config = property(lambda self: 1 / 0)
        mgr = HookManager(cm2)
        assert mgr.has_hooks(HookEvent.PRE_TOOL_USE) is False
        outcome = await mgr.run_event(HookEvent.PRE_TOOL_USE, {}, session_context=_session_ctx())
        assert outcome.fired is False


# --------------------------------------------------------------- contract


class TestExitCodeContract:
    async def test_exit_zero_empty_stdout_is_continue(self, tmp_path):
        script = _write_hook(tmp_path, "noop", f'#!{sys.executable}\nimport sys; sys.exit(0)')
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="n", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {"a": 1}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "continue"
        assert outcome.fired is True
        assert outcome.payload == {"tool_name": "t", "tool_args": {"a": 1}}

    async def test_exit_zero_with_continue_json(self, tmp_path):
        body = f"""\
        #!{sys.executable}
        import json, sys
        payload = json.load(sys.stdin)
        print(json.dumps({{"decision": "continue"}}))
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "cont", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="c", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "continue"
        assert outcome.fired is True

    async def test_exit_code_two_is_deny_with_stderr_reason(self, tmp_path):
        body = f"""\
        #!{sys.executable}
        import sys
        sys.stderr.write("Writes outside /workspace are blocked by policy")
        sys.exit(2)
        """
        script = _write_hook(tmp_path, "block", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="b", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "deny"
        assert "Writes outside /workspace" in outcome.reason

    async def test_exit_other_is_error_then_on_error(self, tmp_path):
        body = f'#!{sys.executable}\nimport sys; sys.exit(1)'
        script = _write_hook(tmp_path, "err", body)
        # deny default for PreToolUse
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="e", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "deny"
        assert "errored" in outcome.reason
        # allow override for PostToolUse
        mgr2 = _make_manager(tmp_path, {"PostToolUse": [HookConfig(name="e", command=[script])]})
        outcome2 = await mgr2.run_event(
            HookEvent.POST_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome2.verdict == "continue"


# --------------------------------------------------------------- modify path


class TestModify:
    async def test_modify_replaces_payload_fields(self, tmp_path):
        body = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        # Rewrite the path argument
        env["payload"]["tool_args"]["path"] = "/tmp/safe.txt"
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "mod", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="m", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "filesystem__write", "tool_args": {"path": "/etc/passwd", "content": "x"}},
            session_context=_session_ctx(), matcher_value="filesystem__write",
        )
        assert outcome.verdict == "modify"
        assert outcome.payload["tool_args"]["path"] == "/tmp/safe.txt"
        assert outcome.payload["tool_args"]["content"] == "x"  # untouched fields preserved

    async def test_modify_chains_forward(self, tmp_path):
        # hook1 sets path; hook2 (sees hook1's output) appends a marker
        body1 = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        env["payload"]["tool_args"]["path"] = "/tmp/safe.txt"
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        body2 = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        env["payload"]["tool_args"]["note"] = "second saw first"
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        s1 = _write_hook(tmp_path, "m1", body1)
        s2 = _write_hook(tmp_path, "m2", body2)
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="m1", command=[s1]),
            HookConfig(name="m2", command=[s2]),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE,
            {"tool_name": "t", "tool_args": {"path": "/etc/passwd"}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "modify"
        assert outcome.payload["tool_args"]["path"] == "/tmp/safe.txt"
        assert outcome.payload["tool_args"]["note"] == "second saw first"

    async def test_modify_with_no_payload_is_ignored(self, tmp_path):
        body = f'#!{sys.executable}\nimport json,sys; print(json.dumps({{"decision":"modify"}})); sys.exit(0)'
        script = _write_hook(tmp_path, "modn", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="m", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {"a": 1}},
            session_context=_session_ctx(), matcher_value="t",
        )
        # No payload change -> stays continue (modify with nothing = no-op)
        assert outcome.verdict == "continue"
        assert outcome.payload == {"tool_name": "t", "tool_args": {"a": 1}}


# ---------------------------------------------------------- composition rules


class TestComposition:
    async def test_deny_short_circuits(self, tmp_path):
        deny = _write_hook(tmp_path, "deny", f'#!{sys.executable}\nimport sys; sys.stderr.write("no"); sys.exit(2)')
        # This hook would modify, but deny should short-circuit before it runs
        cont = _write_hook(tmp_path, "cont", f'#!{sys.executable}\nimport sys; sys.exit(0)')
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="deny", command=[deny]),
            HookConfig(name="cont", command=[cont]),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "deny"
        assert outcome.hook_names == ["deny"]  # second hook never ran

    async def test_require_approval_is_sticky(self, tmp_path):
        esc = _write_hook(tmp_path, "esc", f'#!{sys.executable}\nimport json,sys; print(json.dumps({{"decision":"require_approval","reason":"policy"}})); sys.exit(0)')
        cont = _write_hook(tmp_path, "cont", f'#!{sys.executable}\nimport sys; sys.exit(0)')
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="esc", command=[esc]),
            HookConfig(name="cont", command=[cont]),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "require_approval"
        assert outcome.reason == "policy"
        assert outcome.hook_names == ["esc", "cont"]

    async def test_require_approval_not_downgraded_by_modify(self, tmp_path):
        esc = _write_hook(tmp_path, "esc", f'#!{sys.executable}\nimport json,sys; print(json.dumps({{"decision":"require_approval"}})); sys.exit(0)')
        mod = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        env["payload"]["tool_args"]["x"] = 1
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        modscript = _write_hook(tmp_path, "mod", mod)
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="esc", command=[esc]),
            HookConfig(name="mod", command=[modscript]),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "require_approval"  # not downgraded to modify
        assert outcome.payload["tool_args"]["x"] == 1  # but modify still applied

    async def test_deny_beats_require_approval(self, tmp_path):
        esc = _write_hook(tmp_path, "esc", f'#!{sys.executable}\nimport json,sys; print(json.dumps({{"decision":"require_approval"}})); sys.exit(0)')
        deny = _write_hook(tmp_path, "deny", f'#!{sys.executable}\nimport sys; sys.stderr.write("blocked"); sys.exit(2)')
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="esc", command=[esc]),
            HookConfig(name="deny", command=[deny]),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "deny"


# -------------------------------------------------------------- security


class TestSecurity:
    async def test_trusted_fields_in_envelope(self, tmp_path):
        """The stdin envelope carries server-stamped identity/compliance."""

        body = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        # write envelope to a side file for inspection
        with open({str(tmp_path / 'seen.json')!r}, "w") as f:
            json.dump(env, f)
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "see", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="s", command=[script])]})
        await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(user_email="real@example.gov", compliance_level=3),
            matcher_value="t",
        )
        seen = json.loads((tmp_path / "seen.json").read_text())
        assert seen["hook_event_name"] == "PreToolUse"
        assert seen["session_id"] == "sess-123"
        assert seen["user_email"] == "real@example.gov"
        assert seen["compliance_level"] == 3
        assert "payload" in seen

    async def test_modify_cannot_spoof_trusted_fields(self, tmp_path):
        """A hook returning identity/compliance in its modify payload cannot
        overwrite the trusted envelope fields -- they are stripped."""
        body = f"""\
        #!{sys.executable}
        import json, sys
        env = json.load(sys.stdin)
        env["payload"]["user_email"] = "attacker@example.gov"
        env["payload"]["compliance_level"] = 99
        env["payload"]["session_id"] = "spoofed"
        env["payload"]["tool_args"] = {{"path": "/tmp/safe"}}
        print(json.dumps({{"decision": "modify", "payload": env["payload"]}}))
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "spoof", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="s", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(user_email="real@example.gov", compliance_level=3),
            matcher_value="t",
        )
        assert outcome.verdict == "modify"
        # Trusted fields are NOT present in the returned payload (stripped).
        assert "user_email" not in outcome.payload
        assert "compliance_level" not in outcome.payload
        assert "session_id" not in outcome.payload
        # Mutable change applied.
        assert outcome.payload["tool_args"]["path"] == "/tmp/safe"

    async def test_env_allowlist_excludes_server_secrets(self, tmp_path):
        """Hook subprocess gets only a minimal env; server-side secrets set in
        the parent process are NOT visible to the hook."""
        body = f"""\
        #!{sys.executable}
        import json, os, sys
        seen = dict(os.environ)
        with open({str(tmp_path / 'env.json')!r}, "w") as f:
            json.dump(seen, f)
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "env", body)
        # Plant a "secret" in the parent env that must NOT leak to the hook.
        os.environ["ATLAS_PROVIDER_API_KEY_SECRET"] = "super-secret-value"
        try:
            mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="e", command=[script])]})
            await mgr.run_event(
                HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
                session_context=_session_ctx(), matcher_value="t",
            )
        finally:
            del os.environ["ATLAS_PROVIDER_API_KEY_SECRET"]
        seen = json.loads((tmp_path / "env.json").read_text())
        assert "ATLAS_PROVIDER_API_KEY_SECRET" not in seen
        assert "ATLAS_CONFIG_DIR" in seen
        assert "ATLAS_PROJECT_DIR" in seen

    async def test_command_interpolation(self, tmp_path):
        """${ATLAS_CONFIG_DIR} in command argv is expanded to the config dir."""
        body = f'#!{sys.executable}\nimport sys; sys.exit(0)'
        target = tmp_path / "interp.py"
        target.write_text(textwrap.dedent(body))
        target.chmod(0o755)
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="i", command=["${ATLAS_CONFIG_DIR}/interp.py"]),
        ]})
        # Point config_dir at tmp_path so interpolation resolves to our script.
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
            config_dir=str(tmp_path), project_dir=str(tmp_path),
        )
        assert outcome.verdict == "continue"
        assert outcome.fired is True


# ---------------------------------------------------------- failure modes


class TestFailureModes:
    async def test_timeout_kills_and_uses_on_error(self, tmp_path):
        body = f"""\
        #!{sys.executable}
        import time, sys
        time.sleep(30)
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "slow", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="s", command=[script], timeout_ms=200),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        # PreToolUse default on_error is deny
        assert outcome.verdict == "deny"
        assert "timeout" in outcome.reason

    async def test_output_over_cap_is_killed_and_treated_as_error(self, tmp_path):
        # The cap must be enforced while reading, not by truncating a buffer that
        # already holds the whole stream: a hook emitting far more than the cap
        # would otherwise be fully buffered in server memory first.
        body = f"""\
        #!{sys.executable}
        import sys
        chunk = "x" * 65536
        try:
            for _ in range(200):          # ~13 MB, well over the 1 MB cap
                sys.stdout.write(chunk)
                sys.stdout.flush()
        except Exception:
            pass
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "flood", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="f", command=[script], timeout_ms=20000),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        # PreToolUse fails closed, and a truncated stream is never parsed as a
        # decision.
        assert outcome.verdict == "deny"
        assert "1 MB" in outcome.reason

    async def test_output_within_the_cap_is_returned_intact(self, tmp_path):
        payload = "y" * 10000
        body = f"""\
        #!{sys.executable}
        import json, sys
        print(json.dumps({{"decision": "modify", "payload": {{"tool_args": {{"blob": "{payload}"}}}}}}))
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "chatty", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="c", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "modify"
        assert outcome.payload["tool_args"]["blob"] == payload

    async def test_missing_executable(self, tmp_path):
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="m", command=["/no/such/exec/here"]),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "deny"
        assert "executable not found" in outcome.reason or "errored" in outcome.reason

    async def test_non_json_stdout_on_exit_zero(self, tmp_path):
        body = f'#!{sys.executable}\nimport sys; print("not json"); sys.exit(0)'
        script = _write_hook(tmp_path, "bad", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="b", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "deny"  # error -> default deny for PreToolUse

    async def test_malformed_decision_json(self, tmp_path):
        body = f'#!{sys.executable}\nimport json,sys; print(json.dumps({{"decision":"bogus"}})); sys.exit(0)'
        script = _write_hook(tmp_path, "bad", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="b", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        assert outcome.verdict == "deny"  # invalid decision -> error -> deny

    async def test_bounded_output(self, tmp_path):
        """A hook emitting >1MB of stdout is treated as error (overflow)."""
        body = f"""\
        #!{sys.executable}
        import sys
        sys.stdout.write("x" * (2 * 1024 * 1024))
        sys.exit(0)
        """
        script = _write_hook(tmp_path, "loud", body)
        mgr = _make_manager(tmp_path, {"PreToolUse": [HookConfig(name="l", command=[script])]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "t", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="t",
        )
        # The huge stdout is not valid JSON -> error -> deny
        assert outcome.verdict == "deny"


# ----------------------------------------------------------- matcher filter


class TestMatcherFilter:
    async def test_non_matching_hook_does_not_fire(self, tmp_path):
        fired = _write_hook(tmp_path, "f", f'#!{sys.executable}\nimport sys; sys.exit(0)')
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="f", command=[fired], matcher="filesystem__.*"),
        ]})
        # matcher_value does not match -> hook skipped, fired=False
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "calc__add", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="calc__add",
        )
        assert outcome.fired is False
        assert outcome.verdict == "continue"

    async def test_matching_hook_fires(self, tmp_path):
        fired = _write_hook(tmp_path, "f", f'#!{sys.executable}\nimport sys; sys.exit(0)')
        mgr = _make_manager(tmp_path, {"PreToolUse": [
            HookConfig(name="f", command=[fired], matcher="filesystem__.*"),
        ]})
        outcome = await mgr.run_event(
            HookEvent.PRE_TOOL_USE, {"tool_name": "filesystem__write", "tool_args": {}},
            session_context=_session_ctx(), matcher_value="filesystem__write",
        )
        assert outcome.fired is True


# ------------------------------------------------------- HookBlockedError


class TestHookBlockedError:
    def test_carries_event_and_reason(self):
        err = HookBlockedError(HookEvent.PRE_LLM_CALL, "policy says no")
        assert err.event == HookEvent.PRE_LLM_CALL
        assert err.reason == "policy says no"
        assert "PreLlmCall" in str(err)
        assert "policy says no" in str(err)
