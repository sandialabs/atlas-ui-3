"""HookManager: loads hook config and runs hooks at lifecycle events.

The manager is the single entry point for the hook system. It:

* reads ``HooksConfig`` from the ``ConfigManager`` (cached there, reloaded via
  the existing ``reload_configs`` path);
* short-circuits with zero overhead when no hooks are registered for an event
  (no subprocess, no payload build -- the common case);
* spawns each matching hook via ``asyncio.create_subprocess_exec`` (argv, never
  ``shell=True``) inside the active async turn, with a per-hook timeout, a
  minimal environment allow-list, and bounded stdout/stderr;
* implements the composition rules (sequential, ``deny`` short-circuits,
  ``require_approval`` sticky) and returns a single ``HookOutcome`` the call
  site translates into local action.

Security model (per GH #713): a hook may *tighten* but never *widen* a boundary.
Trusted envelope fields (``session_id``, ``user_email``, ``compliance_level``)
are always re-asserted server-side and never taken from hook output. The
mutable ``payload`` is the only part a hook may replace, and call sites
re-apply security-critical invariants (e.g. re-inject ``_atlas_user`` into tool
args) after applying a ``modify``. See ``docs/admin/hooks.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from atlas.core.telemetry import set_attrs, start_span

from .models import HookConfig, HookDecision, HookEvent, HooksConfig

logger = logging.getLogger(__name__)

# Hard cap on hook stdout/stderr so a chatty hook cannot balloon memory. Overflow
# is treated as a hook error (handled per ``on_error``) and logged.
_MAX_OUTPUT_BYTES = 1 * 1024 * 1024  # 1 MB

# Verdict severity ordering for the "most-restrictive-wins" composition rule.
# deny > require_approval > modify > continue. A later hook can raise the
# severity but never lower it (a ``require_approval`` cannot be downgraded).
_VERDICT_RANK = {"continue": 0, "modify": 1, "require_approval": 2, "deny": 3}
_VERDICT_NAMES = {v: k for k, v in _VERDICT_RANK.items()}


class HookBlockedError(Exception):
    """Raised by call sites when a hook returns ``deny`` and the operation has
    no graceful in-band return value (e.g. PreLlmCall inside the LLM caller).

    Carries the human-readable ``reason`` so upper layers can surface it to the
    user. It deliberately is NOT a subclass of any LLM/domain error so the RAG
    fallback ``except Exception`` path can re-raise it cleanly (see
    ``call_with_rag``).
    """

    def __init__(self, event: HookEvent, reason: str):
        self.event = event
        self.reason = reason
        super().__init__(f"Hook {event.value} blocked: {reason}")


@dataclass
class HookOutcome:
    """The aggregated result of running all hooks for one event.

    Call sites branch on ``verdict``:
        * ``continue``   -- proceed with the original payload (no hooks fired,
                            or every hook returned continue).
        * ``modify``      -- proceed, but apply ``payload`` (the mutable part
                            after all modify decisions), then re-apply
                            security-critical invariants.
        * ``require_approval`` -- escalate to the approval gate (PreToolUse /
                            PermissionRequest) before proceeding; ``payload`` is
                            still the (possibly modified) mutable state.
        * ``deny``       -- block the operation; surface ``reason`` to the
                            model/user.

    ``fired`` is False when no hook ran at all (no config / no match), which lets
    call sites skip downstream translation cheaply.

    ``modified`` is the flag call sites must check before applying ``payload``.
    It is independent of ``verdict``: verdicts are ranked (deny >
    require_approval > modify), so a chain where one hook redacts and a later
    hook escalates reports ``verdict == "require_approval"`` while still
    carrying a rewritten payload. Gating payload application on
    ``verdict == "modify"`` would silently drop that redaction.
    """

    verdict: str = "continue"
    reason: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    fired: bool = False
    modified: bool = False
    hook_names: List[str] = field(default_factory=list)


class HookManager:
    """Loads hook config and dispatches events to subprocess hooks."""

    def __init__(self, config_manager: Any):
        self.config_manager = config_manager

    # ------------------------------------------------------------------ config

    @property
    def hooks_config(self) -> HooksConfig:
        """Return the cached ``HooksConfig`` from the ConfigManager.

        Delegating (rather than caching here) means ``reload_configs`` and the
        conftest ``_isolate_config_cache`` fixture transparently cover hooks.
        """
        return self.config_manager.hooks_config

    def has_hooks(self, event: HookEvent) -> bool:
        """True if at least one hook is registered for ``event``.

        This is the zero-overhead gate call sites check before building a
        payload. When no ``config/hooks.json`` exists this is False and the
        hot path pays only a cached-property lookup.
        """
        try:
            return bool(self.hooks_config.hooks_for(event.value))
        except Exception:
            # A broken config must not take down the chat turn; treat as empty.
            # Log it, though: this silently disables the fail-closed events too,
            # and without a diagnostic that is indistinguishable from "no hooks
            # configured".
            logger.exception(
                "Failed to read hooks config for event %s; treating as no hooks "
                "(fail-closed controls for this event are NOT in effect)",
                event.value,
            )
            return False

    # -------------------------------------------------------------- directories

    def _dirs(self) -> Tuple[str, str]:
        """Resolve ``(config_dir, project_dir)`` for env injection/interpolation.

        ``ATLAS_CONFIG_DIR`` is the user config dir (where ``hooks.json`` and
        operator scripts live); ``ATLAS_PROJECT_DIR`` is the project root. Both
        are derived from the ConfigManager so they track the same path logic as
        every other config file.
        """
        try:
            atlas_root = Path(self.config_manager._atlas_root)
            project_dir = str(atlas_root.parent)
            app_config_dir = Path(self.config_manager.app_settings.app_config_dir)
            if not app_config_dir.is_absolute():
                app_config_dir = atlas_root.parent / app_config_dir
            return str(app_config_dir), project_dir
        except Exception:
            return os.environ.get("ATLAS_CONFIG_DIR", ""), os.environ.get("ATLAS_PROJECT_DIR", "")

    # ------------------------------------------------------------------- run

    async def run_event(
        self,
        event: HookEvent,
        payload: Dict[str, Any],
        *,
        session_context: Optional[Dict[str, Any]] = None,
        matcher_value: Optional[str] = None,
        config_dir: Optional[str] = None,
        project_dir: Optional[str] = None,
    ) -> HookOutcome:
        """Run all matching hooks for ``event`` and return the aggregated outcome.

        Zero-overhead when no hooks are registered: returns a ``continue``
        outcome with ``fired=False`` without spawning any process.
        """
        # Resolve hooks (failure-tolerant: empty config -> no-op).
        try:
            hooks = self.hooks_config.hooks_for(event.value)
        except Exception as e:
            logger.warning("hooks: failed to load hooks config for %s: %s", event.value, e)
            return HookOutcome(payload=dict(payload))

        if not hooks:
            return HookOutcome(payload=dict(payload))

        # Resolve dirs once (env interpolation + subprocess env).
        if config_dir is None or project_dir is None:
            d_config, d_project = self._dirs()
            config_dir = config_dir or d_config
            project_dir = project_dir or d_project

        sc = session_context or {}
        # Trusted envelope fields are stamped server-side and re-asserted after
        # every modify: a hook can never widen or spoof identity/compliance.
        envelope = {
            "hook_event_name": event.value,
            "session_id": sc.get("session_id"),
            "user_email": sc.get("user_email"),
            "compliance_level": sc.get("compliance_level"),
            "payload": dict(payload),
        }

        verdict_rank = 0  # continue
        reason: Optional[str] = None
        fired = False
        modified = False
        hook_names: List[str] = []

        with start_span("hook.event", {
            "hook_event": event.value,
            "hook_count": len(hooks),
            "matcher_value": matcher_value,
        }) as span:
            for hook in hooks:
                if not hook.matches(matcher_value):
                    continue
                fired = True
                hook_names.append(hook.name)
                decision = await self._run_one(
                    hook, event, envelope, config_dir, project_dir, span
                )
                # --- apply decision to the running state ---
                d = decision.decision
                if d == "deny":
                    verdict_rank = _VERDICT_RANK["deny"]
                    reason = decision.reason or f"Hook '{hook.name}' denied the operation"
                    set_attrs(span, {
                        "hook.denied_by": hook.name,
                        "hook.deny_reason_present": reason is not None,
                    })
                    break  # first deny short-circuits the chain
                if d == "require_approval":
                    if verdict_rank < _VERDICT_RANK["require_approval"]:
                        verdict_rank = _VERDICT_RANK["require_approval"]
                        reason = decision.reason
                if d == "modify":
                    if self._apply_modify(envelope, decision, hook):
                        # Track the mutation separately from the verdict rank: a
                        # later require_approval outranks "modify" and would
                        # otherwise hide that the payload was rewritten.
                        modified = True
                        if verdict_rank < _VERDICT_RANK["modify"]:
                            verdict_rank = _VERDICT_RANK["modify"]
                # continue: no state change
            set_attrs(span, {
                "hook.verdict": _VERDICT_NAMES[verdict_rank],
                "hook.fired_count": len(hook_names),
            })

        outcome = HookOutcome(
            verdict=_VERDICT_NAMES[verdict_rank],
            reason=reason,
            payload=envelope["payload"],
            fired=fired,
            modified=modified,
            hook_names=hook_names,
        )
        # Re-assert trusted fields: even though we only ever read them from the
        # server-stamped envelope, defend-in-depth in case a modify payload tried
        # to include spoofed identity/compliance keys.
        outcome.payload.pop("session_id", None)
        outcome.payload.pop("user_email", None)
        outcome.payload.pop("compliance_level", None)
        return outcome

    # ------------------------------------------------------------- internals

    def _apply_modify(self, envelope: Dict[str, Any], decision: HookDecision, hook: HookConfig) -> bool:
        """Merge a ``modify`` decision's payload into the running envelope.

        Full-replacement semantics (GH #713 open question #2): the hook returns
        the mutable part it wants; we replace the keys it specifies. Trusted
        envelope fields are never taken from hook output. Unknown payload keys
        are accepted -- the call site validates against the event schema.

        Returns True if any mutable field was actually changed (so the verdict
        is only bumped to ``modify`` when something really changed).
        """
        new_payload = decision.payload
        if not isinstance(new_payload, dict) or not new_payload:
            logger.debug("hooks: hook %s returned modify with no payload; ignoring", hook.name)
            return False
        # Strip any attempt to overwrite trusted identity/compliance via payload.
        for k in ("session_id", "user_email", "compliance_level"):
            new_payload.pop(k, None)
        if not new_payload:
            return False
        envelope["payload"].update(new_payload)
        return True

    async def _run_one(
        self,
        hook: HookConfig,
        event: HookEvent,
        envelope: Dict[str, Any],
        config_dir: str,
        project_dir: str,
        span: Any,
    ) -> HookDecision:
        """Spawn one hook subprocess and parse its decision.

        Failure modes (timeout, crash, malformed output) map to ``on_error``:
        ``deny`` -> a deny decision; ``allow`` -> a continue decision. Always
        logged and recorded on the span so the audit trail is intact.
        """
        timeout_s = hook.timeout_ms / 1000.0
        argv = self._interpolate_command(hook.command, config_dir, project_dir)
        env = self._build_env(config_dir, project_dir)
        stdin_bytes = (json.dumps(envelope) + "\n").encode("utf-8")

        t0 = asyncio.get_running_loop().time()
        hook_attrs = {"hook.name": hook.name, "hook.timeout_ms": hook.timeout_ms}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            logger.error("hooks: hook %s executable not found: %s", hook.name, argv[0])
            set_attrs(span, {**hook_attrs, "hook.error": "executable_not_found"})
            return self._error_decision(hook, event, "executable not found")
        except Exception as e:
            logger.error("hooks: hook %s failed to spawn: %s", hook.name, e, exc_info=True)
            set_attrs(span, {**hook_attrs, "hook.error": f"spawn_error:{type(e).__name__}"})
            return self._error_decision(hook, event, f"spawn error: {e}")

        try:
            stdout, stderr, overflowed = await asyncio.wait_for(
                self._communicate_bounded(proc, stdin_bytes), timeout=timeout_s
            )
        except asyncio.TimeoutError:
            await self._kill(proc)
            logger.warning("hooks: hook %s timed out after %dms", hook.name, hook.timeout_ms)
            set_attrs(span, {**hook_attrs, "hook.error": "timeout", "hook.timed_out": True})
            return self._error_decision(hook, event, "timeout")
        except Exception as e:
            await self._kill(proc)
            logger.error("hooks: hook %s I/O error: %s", hook.name, e, exc_info=True)
            set_attrs(span, {**hook_attrs, "hook.error": f"io_error:{type(e).__name__}"})
            return self._error_decision(hook, event, f"io error: {e}")

        if overflowed:
            # The child was killed mid-stream, so what we hold is a prefix. Parsing
            # it would risk misreading truncated JSON as a decision (or as non-JSON
            # garbage); treat overflow as a hook error and let on_error decide.
            logger.warning(
                "hooks: hook %s exceeded the %d-byte output cap; killed and treated as error",
                hook.name, _MAX_OUTPUT_BYTES,
            )
            set_attrs(span, {**hook_attrs, "hook.error": "output_overflow"})
            return self._error_decision(hook, event, "output exceeded 1 MB cap")

        duration_ms = int((asyncio.get_running_loop().time() - t0) * 1000)
        stdout_s = stdout.decode("utf-8", "replace")
        stderr_s = stderr.decode("utf-8", "replace")
        set_attrs(span, {
            **hook_attrs,
            "hook.exit_code": proc.returncode,
            "hook.duration_ms": duration_ms,
            "hook.stdout_size": len(stdout_s),
            "hook.stderr_size": len(stderr_s),
        })

        return self._parse_decision(hook, event, proc.returncode, stdout_s, stderr_s)

    def _parse_decision(
        self,
        hook: HookConfig,
        event: HookEvent,
        returncode: Optional[int],
        stdout_s: str,
        stderr_s: str,
    ) -> HookDecision:
        """Translate (exit code, stdout, stderr) into a ``HookDecision``.

        Fast path (no JSON needed):
            0  -> continue (apply stdout JSON if present, else no-op)
            2  -> deny, reason = stderr
            other -> error -> on_error
        """
        if returncode == 0:
            if not stdout_s.strip():
                return HookDecision(decision="continue")
            try:
                data = json.loads(stdout_s)
            except json.JSONDecodeError:
                logger.warning(
                    "hooks: hook %s exited 0 but emitted non-JSON stdout; treating as error",
                    hook.name,
                )
                return self._error_decision(hook, event, "non-JSON stdout on exit 0")
            try:
                return HookDecision(**data) if isinstance(data, dict) else HookDecision(decision="continue")
            except Exception as e:
                logger.warning("hooks: hook %s emitted invalid decision JSON: %s", hook.name, e)
                return self._error_decision(hook, event, f"invalid decision JSON: {e}")

        if returncode == 2:
            reason = (stderr_s.strip() or f"Hook '{hook.name}' blocked the operation")
            return HookDecision(decision="deny", reason=reason[:1000])

        logger.warning(
            "hooks: hook %s exited with code %s (stderr: %s)",
            hook.name, returncode, stderr_s.strip()[:200] or "<empty>",
        )
        return self._error_decision(hook, event, f"exit code {returncode}")

    def _error_decision(self, hook: HookConfig, event: HookEvent, detail: str) -> HookDecision:
        """Map a hook failure to its ``on_error`` outcome."""
        on_error = hook.effective_on_error(event)
        if on_error == "deny":
            return HookDecision(decision="deny", reason=f"Hook '{hook.name}' errored: {detail}")
        return HookDecision(decision="continue")

    @staticmethod
    async def _kill(proc: Any) -> None:
        """Kill a hook subprocess and reap it, tolerating an already-dead child."""
        try:
            proc.kill()
        except ProcessLookupError:
            # Already exited between the check and the signal -- nothing to kill.
            pass
        try:
            await proc.wait()
        except Exception:  # pragma: no cover - defensive
            # Reaping is best-effort; a failure here must not mask the original
            # timeout/IO error the caller is about to report.
            logger.debug("hooks: failed to reap killed hook process", exc_info=True)

    @classmethod
    async def _communicate_bounded(
        cls, proc: Any, stdin_bytes: bytes
    ) -> Tuple[bytes, bytes, bool]:
        """Feed stdin and read stdout/stderr with the cap enforced *at read time*.

        ``proc.communicate()`` buffers the entire stream before returning, so a
        hook emitting hundreds of megabytes would exhaust server memory no matter
        what we truncated afterwards. Here each stream is read in chunks and the
        child is killed as soon as their combined size crosses
        ``_MAX_OUTPUT_BYTES``. Returns ``(stdout, stderr, overflowed)``.
        """
        total = 0
        overflowed = False

        async def _feed() -> None:
            if proc.stdin is None:
                return
            try:
                proc.stdin.write(stdin_bytes)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                # A hook that ignores stdin and exits early is legitimate (e.g. an
                # unconditional allow); its exit code still decides the outcome.
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:  # pragma: no cover - defensive
                    logger.debug("hooks: failed to close hook stdin", exc_info=True)

        async def _drain(stream: Any) -> bytes:
            nonlocal total, overflowed
            if stream is None:
                return b""
            buf = bytearray()
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return bytes(buf)
                total += len(chunk)
                if total > _MAX_OUTPUT_BYTES:
                    overflowed = True
                    await cls._kill(proc)
                    return bytes(buf)
                buf += chunk

        _, stdout, stderr = await asyncio.gather(
            _feed(), _drain(proc.stdout), _drain(proc.stderr)
        )
        if not overflowed:
            await proc.wait()
        return stdout, stderr, overflowed

    @staticmethod
    def _interpolate_command(command: List[str], config_dir: str, project_dir: str) -> List[str]:
        """Expand ``${ATLAS_CONFIG_DIR}`` / ``${ATLAS_PROJECT_DIR}`` in argv.

        Only these two names are expanded -- never arbitrary env vars -- so a
        hook config cannot exfiltrate the server's environment into its own
        command line.
        """
        mapping = {"ATLAS_CONFIG_DIR": config_dir, "ATLAS_PROJECT_DIR": project_dir}
        out = []
        for arg in command:
            for name, val in mapping.items():
                arg = arg.replace(f"${{{name}}}", val)
            out.append(arg)
        return out

    @staticmethod
    def _build_env(config_dir: str, project_dir: str) -> Dict[str, str]:
        """Minimal environment for hook subprocesses.

        Hooks get a small allow-list (``PATH``/``HOME`` so interpreters resolve)
        plus ``ATLAS_CONFIG_DIR`` / ``ATLAS_PROJECT_DIR``. They do NOT inherit the
        server's full environment, which holds provider API keys and other
        secrets.
        """
        env: Dict[str, str] = {}
        for key in ("PATH", "HOME", "SYSTEMROOT", "LANG", "LC_ALL", "USER"):
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        env["ATLAS_CONFIG_DIR"] = config_dir
        env["ATLAS_PROJECT_DIR"] = project_dir
        return env


# ----------------------------------------------------------------- singleton

_hook_manager: Optional[HookManager] = None


def get_hook_manager() -> Optional[HookManager]:
    """Return the process-wide ``HookManager`` singleton, or None on init failure.

    Mirrors ``get_approval_manager`` / ``get_compliance_manager``. Lazily binds
    to the module-level ``config_manager`` singleton so hook config follows the
    same load/reload story as the rest of Atlas config.
    """
    global _hook_manager
    if _hook_manager is None:
        try:
            from atlas.modules.config.config_manager import config_manager as _cm

            _hook_manager = HookManager(_cm)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("hooks: could not initialize hook manager: %s", e)
            return None
    return _hook_manager


def set_hook_manager_for_testing(mgr: Optional[HookManager]) -> None:
    """Replace the singleton hook manager for tests. Restore with ``None``."""
    global _hook_manager
    _hook_manager = mgr
