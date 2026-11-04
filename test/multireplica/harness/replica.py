"""Independent Atlas API processes ("replicas") sharing one Redis and Postgres.

Each replica is a real ``uvicorn atlas.main:app`` process on its own fixed
port, in its own process group (so its MCP server children go down with it),
with its own working directory, log and ``.env``. Nothing load-balances
between them: a test chooses which replica receives each request.

The replica environment is built from scratch rather than inherited, so the
developer's shell, ``.env`` and any leaked deployment variables cannot change
what the suite tests.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import httpx

from .manifest import record_process

REPO_ROOT = Path(__file__).resolve().parents[3]

ADMIN_USER = "mr-admin@example.com"

# Variables a child process needs to run at all; everything else is explicit.
_PASSTHROUGH = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "XDG_RUNTIME_DIR")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_is_free(port: int) -> bool:
    """True when a new server could listen on ``port`` now.

    ``SO_REUSEADDR`` matches what uvicorn sets, so a port whose old connections
    sit in ``TIME_WAIT`` after a replica stopped still counts as free.
    """
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def base_env(extra: Dict[str, str]) -> Dict[str, str]:
    env = {k: os.environ[k] for k in _PASSTHROUGH if k in os.environ}
    env["PYTHONPATH"] = str(REPO_ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    # litellm calls ``load_dotenv()`` at import time, which walks up from the
    # installed package and loads the first ``.env`` it finds -- with a venv
    # inside a checkout, the developer's own ``.env``. Disable python-dotenv so
    # no file outside the harness can configure a replica.
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.update(extra)
    return env


@dataclass
class Replica:
    name: str
    port: int
    workdir: Path
    env: Dict[str, str]
    proc: Optional[subprocess.Popen] = None
    paused: bool = False
    _log_handle: Optional[object] = field(default=None, repr=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def log_path(self) -> Path:
        return self.workdir / "replica.log"

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, timeout: float = 90.0) -> None:
        if self.running:
            return
        if not port_is_free(self.port):
            raise RuntimeError(f"{self.name}: port {self.port} is still in use")
        # ``settings`` reads ``../.env`` relative to the working directory and
        # main.py loads it too; point both at this replica's own (empty) file.
        run_dir = self.workdir / "cwd"
        run_dir.mkdir(parents=True, exist_ok=True)
        (self.workdir / ".env").write_text("# multireplica harness: configuration is in the environment\n")
        self._log_handle = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "atlas.main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=run_dir,
            env=self.env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        record_process(f"replica-{self.name}", self.proc.pid)
        self.paused = False
        self.wait_healthy(timeout)

    def wait_healthy(self, timeout: float = 90.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running:
                raise RuntimeError(f"{self.name} exited during startup:\n{self.log_tail()}")
            try:
                if httpx.get(self.url + "/api/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass  # not listening yet; keep polling until the deadline
            time.sleep(0.5)
        raise RuntimeError(f"{self.name} not healthy after {timeout:.0f}s:\n{self.log_tail()}")

    def _signal_group(self, sig: int) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                pass  # the group already exited between poll() and killpg()

    def kill(self) -> None:
        """Crash the replica: SIGKILL its whole process group, no teardown."""
        if self.paused:
            self._signal_group(signal.SIGCONT)
        self._signal_group(signal.SIGKILL)
        self._reap()

    def stop(self, timeout: float = 15.0) -> None:
        """Graceful shutdown (SIGTERM), escalating to SIGKILL."""
        if self.paused:
            self.resume()
        self._signal_group(signal.SIGTERM)
        if self.proc is not None:
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self._signal_group(signal.SIGKILL)
        self._reap()

    def pause(self) -> None:
        """Freeze the replica (SIGSTOP) -- a stand-in for a long GC pause or partition."""
        self._signal_group(signal.SIGSTOP)
        self.paused = True

    def resume(self) -> None:
        self._signal_group(signal.SIGCONT)
        self.paused = False

    def restart(self) -> None:
        self.kill()
        self.start()

    def _reap(self) -> None:
        if self.proc is not None:
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                # Unkillable (e.g. stuck in D state); drop the handle anyway
                # and let the port wait below report what is left.
                pass
        self.proc = None
        self.paused = False
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        # The listening socket can linger briefly after the process dies.
        deadline = time.monotonic() + 10
        while not port_is_free(self.port) and time.monotonic() < deadline:
            time.sleep(0.2)

    def log_tail(self, lines: int = 60) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except FileNotFoundError:
            return "(no log)"


class MockLLM:
    """The scripted OpenAI-compatible model every replica talks to."""

    def __init__(self, workdir: Path):
        self.port = free_port()
        self.workdir = workdir
        self.proc: Optional[subprocess.Popen] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        log = open(self.workdir / "mock_llm.log", "ab")
        self.proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("mock_llm.py"))],
            env=base_env({"MOCK_LLM_PORT": str(self.port)}),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.close()
        record_process("mock-llm", self.proc.pid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                if httpx.get(self.url + "/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.3)
        log_text = (self.workdir / "mock_llm.log").read_text(errors="replace")[-2000:]
        self.stop()
        raise RuntimeError(f"mock LLM did not start:\n{log_text}")

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # exited between poll() and killpg()
            self.proc.wait(10)


def write_config(config_dir: Path, mock_llm_url: str) -> None:
    """llmconfig.yml + mcp.json shared by every replica."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "llmconfig.yml").write_text(
        "models:\n"
        "  scripted-mock:\n"
        f'    model_url: "{mock_llm_url}/v1"\n'
        '    model_name: "openai/scripted-mock"\n'
        '    api_key: "mock"\n'
        '    description: "Scripted mock model for the multi-replica harness"\n'
        "    supports_tools: true\n"
    )
    import json

    (config_dir / "mcp.json").write_text(json.dumps({
        "harness": {
            "command": [sys.executable, str(Path(__file__).with_name("side_effect_mcp.py"))],
            # Atlas starts stdio servers with a scrubbed environment; hand the
            # tool its settings explicitly, expanded from each replica's env.
            "env": {
                "HARNESS_SIDE_EFFECT_DIR": "${HARNESS_SIDE_EFFECT_DIR}",
                "HARNESS_REPLICA": "${HARNESS_REPLICA}",
            },
            "groups": ["users"],
            "description": "Multi-replica harness side-effect counter",
            "compliance_level": "Public",
        }
    }, indent=2))
    (config_dir / "rag-sources.json").write_text("{}")


def replica_env(
    *,
    name: str,
    port: int,
    workdir: Path,
    config_dir: Path,
    side_effect_dir: Path,
    redis_url: str,
    redis_key_prefix: str,
    postgres_url: str,
    secrets: Dict[str, str],
    overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    env = {
        "PORT": str(port),
        # DEBUG_MODE (X-User-Email trusted without a proxy) is refused when
        # ENVIRONMENT is production, the default; replicas bind to loopback.
        "DEBUG_MODE": "true",
        "ENVIRONMENT": "development",
        "ATLAS_HOST": "127.0.0.1",
        "APP_CONFIG_DIR": str(config_dir),
        "APP_LOG_DIR": str(workdir / "logs"),
        # Shared state: what makes these processes replicas of one deployment.
        "SESSION_REPOSITORY_TYPE": "redis",
        "REDIS_URL": redis_url,
        "REDIS_KEY_PREFIX": redis_key_prefix,
        "CHAT_HISTORY_DB_URL": postgres_url,
        # Features the run paths need.
        "FEATURE_CHAT_HISTORY_ENABLED": "true",
        "FEATURE_AGENT_MODE_AVAILABLE": "true",
        "FEATURE_TOOLS_ENABLED": "true",
        "FEATURE_WORKSPACES_ENABLED": "true",
        "FEATURE_RAG_ENABLED": "false",
        "FEATURE_AGENT_RUNS_API_ENABLED": "true",
        "REQUIRE_TOOL_APPROVAL_BY_DEFAULT": "false",
        "FORCE_TOOL_APPROVAL_GLOBALLY": "false",
        # The registry's built-in default. Until the cap is applied at startup
        # (see test_configured_cap_applies_before_any_websocket) any other value
        # only takes effect once a browser connects to that replica.
        "MAX_CONCURRENT_RUNS_PER_USER": "5",
        "USE_MOCK_S3": "true",
        # Admin identity for /admin/coordination (issue #1129).
        "ADMIN_USERS": ADMIN_USER,
        # Names the replica in [COORD] log lines and metrics.
        "ATLAS_REPLICA_ID": f"replica-{name}",
        "LLM_MAX_RETRIES": "0",
        # Side-effect tool bookkeeping (inherited by the MCP child process).
        "HARNESS_SIDE_EFFECT_DIR": str(side_effect_dir),
        "HARNESS_REPLICA": name,
        # Must be identical on every replica, as in a real deployment.
        **secrets,
    }
    env.update(overrides or {})
    (workdir / "logs").mkdir(parents=True, exist_ok=True)
    return base_env(env)
