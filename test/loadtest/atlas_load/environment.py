"""The system under load: N Atlas replicas, shared Redis/Postgres and mocks.

Built from the multi-replica harness (``test/multireplica/harness``, PR #1146)
so a load run and the correctness suite start replicas the same way: real
``uvicorn atlas.main:app`` processes with environments built from scratch,
sharing one Redis (sessions) and one PostgreSQL (chat history). On top of the
harness this adds what a load profile needs:

- the scripted mock LLM wrapped with request accounting
  (``load_mock_llm.py``), and, for ``auth: "oidc"`` profiles, JWT checks;
- for ``auth: "oidc"``: the synthetic IdP (``mock_oidc.py``), Atlas started as
  an OIDC relying party with header auth **off** (so every user really logs
  in), RFC 8693 delegation on, and a ``delegated-mock`` model whose every
  call needs an exchanged token.

``RemoteTarget`` is the same interface for an already-running deployment
(``--target``): no processes to start or sample.
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "multireplica"))

from harness.cluster import Cluster  # noqa: E402
from harness.infra import start_shared_infra  # noqa: E402
from harness.manifest import record_process  # noqa: E402
from harness.replica import base_env, free_port  # noqa: E402

OIDC_CLIENT_ID = "atlas-loadtest"
DELEGATED_AUDIENCE = "mock-litellm"


class MockService:
    """A mock started as its own process group (so it dies with the run)."""

    def __init__(self, name: str, script: Path, workdir: Path, env: Dict[str, str], port_var: str):
        self.name = name
        self.port = free_port()
        self.script = script
        self.workdir = workdir
        self.env = base_env({**env, port_var: str(self.port)})
        self.proc: Optional[subprocess.Popen] = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        log_path = self.workdir / f"{self.name}.log"
        with open(log_path, "ab") as log:
            self.proc = subprocess.Popen([sys.executable, str(self.script)], env=self.env, stdout=log,
                                         stderr=subprocess.STDOUT, start_new_session=True)
        record_process(self.name, self.proc.pid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and self.proc.poll() is None:
            try:
                if httpx.get(self.url + "/health", timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass  # not listening yet
            time.sleep(0.2)
        self.stop()
        raise RuntimeError(f"{self.name} did not start:\n{log_path.read_text(errors='replace')[-2000:]}")

    def stats(self) -> Dict[str, Any]:
        try:
            return httpx.get(self.url + "/stats", timeout=5).json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"error": type(exc).__name__}

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # already gone
            self.proc.wait(10)


def _write_delegated_model(config_dir: Path, llm_url: str) -> None:
    """Append a model whose credential is an RFC 8693-exchanged user token."""
    with open(config_dir / "llmconfig.yml", "a") as handle:
        handle.write(
            "  delegated-mock:\n"
            f'    model_url: "{llm_url}/v1"\n'
            '    model_name: "openai/scripted-mock"\n'
            '    api_key_source: "delegated"\n'
            "    delegation:\n"
            f'      audience: "{DELEGATED_AUDIENCE}"\n'
            '      scope: "llm.invoke"\n'
            '    description: "Scripted mock behind delegated (token-exchanged) auth"\n'
            "    supports_tools: true\n"
        )


class LocalEnvironment:
    """Starts and owns every process of a local load run."""

    local = True

    def __init__(self, profile: Dict[str, Any], workdir: Path):
        self.profile = profile
        self.workdir = workdir
        self.auth = profile.get("auth", "header")
        self.infra = None
        self.idp: Optional[MockService] = None
        self.llm: Optional[MockService] = None
        self.cluster: Optional[Cluster] = None

    def start(self) -> None:
        self.infra = start_shared_infra()
        llm_env: Dict[str, str] = {}
        overrides: Dict[str, str] = {
            # One load client on loopback looks like a single abusive IP to the
            # per-IP rate limiter; profiles measure Atlas, not that limiter.
            "RATE_LIMIT_RPM": "100000000",
            "MAX_CONCURRENT_RUNS_PER_USER": str(self.profile.get("max_runs_per_user", 5)),
        }
        if self.auth == "oidc":
            self.idp = MockService("mock-oidc", HERE / "mock_oidc.py", self.workdir, {
                "OIDC_CLIENT_ID": OIDC_CLIENT_ID, "OIDC_CLIENT_SECRET": "load-secret",
                "OIDC_ALLOWED_AUDIENCES": f"atlas,{DELEGATED_AUDIENCE}",
            }, "MOCK_OIDC_PORT")
            # The issuer must be the URL Atlas and the mocks use; the port is
            # known before the process starts.
            self.idp.env["OIDC_ISSUER"] = self.idp.url
            self.idp.start()
            llm_env = {"MOCK_LLM_JWKS_URL": self.idp.url + "/jwks", "MOCK_LLM_AUDIENCE": DELEGATED_AUDIENCE}
            overrides.update({
                "FEATURE_OIDC_AUTH_ENABLED": "true",
                "FEATURE_HEADER_AUTH_ENABLED": "false",
                "OIDC_ISSUER": self.idp.url,
                "OIDC_CLIENT_ID": OIDC_CLIENT_ID,
                "OIDC_CLIENT_SECRET": "load-secret",
                "OIDC_SESSION_SECRET": secrets.token_urlsafe(32),
                "FEATURE_OIDC_DELEGATION_ENABLED": "true",
                "OIDC_DELEGATION_PROVIDER": "token_exchange",
            })
        self.llm = MockService("mock-llm", HERE / "load_mock_llm.py", self.workdir, llm_env, "MOCK_LLM_PORT")
        self.llm.start()
        overrides.update(self.profile.get("env", {}))
        self.cluster = Cluster(self.workdir, self.infra, self.llm, size=int(self.profile.get("replicas", 2)),
                               overrides=overrides)
        if self.auth == "oidc":
            _write_delegated_model(self.cluster.config_dir, self.llm.url)
        self.cluster.start()

    @property
    def replicas(self) -> List[Any]:
        return self.cluster.replicas if self.cluster else []

    @property
    def urls(self) -> List[str]:
        return [r.url for r in self.replicas]

    @property
    def service_key(self) -> Optional[str]:
        return self.cluster.api_key if self.cluster else None

    def mock_stats(self) -> Dict[str, Any]:
        stats = {}
        if self.llm:
            stats["llm"] = self.llm.stats()
        if self.idp:
            stats["idp"] = self.idp.stats()
        return stats

    def describe(self) -> Dict[str, Any]:
        return {
            "mode": "local",
            "replicas": [{"name": r.name, "url": r.url} for r in self.replicas],
            "auth": self.auth,
            "redis": "podman" if not os.environ.get("ATLAS_TEST_REDIS_URL") else "ATLAS_TEST_REDIS_URL",
            "postgres": "podman" if not os.environ.get("ATLAS_TEST_POSTGRES_URL") else "ATLAS_TEST_POSTGRES_URL",
            "workdir": str(self.workdir),
        }

    def close(self) -> None:
        for step in (lambda: self.cluster and self.cluster.close(),
                     lambda: self.llm and self.llm.stop(),
                     lambda: self.idp and self.idp.stop(),
                     lambda: self.infra and self.infra.close()):
            try:
                step()
            except Exception as exc:  # teardown must run every step
                print(f"[loadtest] cleanup step failed: {exc}", file=sys.stderr)


class RemoteTarget:
    """An already-running deployment: replica (or load-balancer) URLs only."""

    local = False

    def __init__(self, profile: Dict[str, Any], urls: List[str], service_key: Optional[str]):
        self.profile = profile
        self.auth = profile.get("auth", "header")
        self._urls = [u.rstrip("/") for u in urls]
        self.service_key = service_key
        self.replicas: List[Any] = []

    def start(self) -> None:
        for url in self._urls:
            httpx.get(url + "/api/health", timeout=10).raise_for_status()

    @property
    def urls(self) -> List[str]:
        return self._urls

    def mock_stats(self) -> Dict[str, Any]:
        return {}

    def describe(self) -> Dict[str, Any]:
        return {"mode": "remote", "targets": self._urls, "auth": self.auth}

    def close(self) -> None:
        pass
