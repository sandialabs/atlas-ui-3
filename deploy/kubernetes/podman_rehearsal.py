#!/usr/bin/env python3
"""Rehearse the staging overlay with podman, without a Kubernetes cluster.

Renders overlays/staging with ``kubectl kustomize`` (offline; no cluster is
contacted) and runs what it describes with an Atlas image: the bundled Redis,
PostgreSQL and MinIO under their Service names on a private podman network,
every API pod's ``migrate`` init container concurrently, then the API
containers with the ConfigMap and Secret environment, gated by the
manifest's own startup-probe command. Then:

1. validate_replicas.py against each API container (health, auth, shared
   PostgreSQL, WebSocket session resumed across replicas);
2. a failover drill: open a session on replica 1, stop it gracefully (SIGTERM,
   like a pod deletion), resume the session on replica 2, start replica 1
   again (a replacement pod) and validate once more.

What it cannot show: scheduling, Service load balancing, ingress, PVC
binding and SCC admission. Those need a cluster; see
docs/admin/kubernetes-staging.md.

Usage: python deploy/kubernetes/podman_rehearsal.py --image localhost/atlas-ui-3:dev [--replicas 3] [--keep]
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import yaml

HERE = Path(__file__).resolve().parent
OVERLAY = HERE / "overlays" / "staging"
VALIDATOR = HERE / "validate_replicas.py"


def run(cmd: List[str], check: bool = True, capture: bool = True, timeout: Optional[float] = None):
    result = subprocess.run(cmd, text=True, capture_output=capture, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} ... failed ({result.returncode}):\n{result.stderr or result.stdout}")
    return result


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fresh_secrets() -> Dict[str, str]:
    pg, redis, s3 = secrets.token_hex(16), secrets.token_hex(16), secrets.token_hex(16)
    return {
        "POSTGRES_PASSWORD": pg,
        "REDIS_PASSWORD": redis,
        "CHAT_HISTORY_DB_URL": f"postgresql://atlas:{pg}@atlas-postgres:5432/atlas_chat_history",
        "REDIS_URL": f"redis://:{redis}@atlas-redis:6379/0",
        "S3_ENDPOINT": "http://atlas-minio:9000",
        "S3_ACCESS_KEY": "atlas" + secrets.token_hex(4),
        "S3_SECRET_KEY": s3,
        "PROXY_SECRET": secrets.token_urlsafe(32),
        "MCP_TOKEN_ENCRYPTION_KEY": secrets.token_urlsafe(32),
        "CAPABILITY_TOKEN_SECRET": secrets.token_urlsafe(32),
    }


def render(secret_values: Dict[str, str]) -> List[dict]:
    """Render the overlay from a scratch copy holding a throwaway secrets.env."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "kubernetes"
        shutil.copytree(HERE, tree, ignore=shutil.ignore_patterns("secrets.env", "__pycache__"))
        # kustomize's secretGenerator reads a file. These are one-run values
        # for throwaway containers, written owner-only inside a private
        # temporary directory that is deleted right after rendering.
        fd = os.open(tree / "overlays" / "staging" / "secrets.env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.writelines(f"{k}={v}\n" for k, v in secret_values.items())
        out = run(["kubectl", "kustomize", str(tree / "overlays" / "staging")]).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def find(docs: List[dict], kind: str, name: str) -> dict:
    for doc in docs:
        if doc["kind"] == kind and doc["metadata"]["name"].startswith(name):
            return doc
    raise KeyError(f"{kind}/{name} not in rendered overlay")


def container_env(container: dict, configmap: dict, secret_values: Dict[str, str]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for source in container.get("envFrom", []):
        if "configMapRef" in source:
            env.update(configmap["data"])
    for item in container.get("env", []):
        if "value" in item:
            env[item["name"]] = item["value"]
        elif "secretKeyRef" in item.get("valueFrom", {}):
            env[item["name"]] = secret_values[item["valueFrom"]["secretKeyRef"]["key"]]
    return env


def env_args(env: Dict[str, str]) -> List[str]:
    args: List[str] = []
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    return args


def k8s_args(container: dict, env: Dict[str, str]) -> List[str]:
    """Expand $(VAR) references in args the way the kubelet does."""
    out = []
    for arg in container.get("args", []):
        for key, value in env.items():
            arg = arg.replace(f"$({key})", value)
        out.append(arg)
    return out


class Rehearsal:
    def __init__(self, image: str, replicas: int, keep: bool):
        self.image = image
        self.replicas = replicas
        self.keep = keep
        self.tag = uuid.uuid4().hex[:6]
        self.network = f"atlas-rehearsal-{self.tag}"
        self.containers: List[str] = []
        self.secrets = fresh_secrets()
        self.docs = render(self.secrets)
        self.configmap = find(self.docs, "ConfigMap", "atlas-api-env")
        self.api = find(self.docs, "Deployment", "atlas-api")
        self.ports: Dict[str, int] = {}
        # Service name -> container IP, passed as --add-host so name lookup
        # does not depend on the host's podman DNS plugin.
        self.hosts: Dict[str, str] = {}

    def name(self, base: str) -> str:
        return f"{base}-{self.tag}"

    def start_backend(self, kind: str, resource: str, alias: str, extra: List[str]) -> None:
        pod = find(self.docs, kind, resource)["spec"]["template"]["spec"]
        container = pod["containers"][0]
        env = container_env(container, self.configmap, self.secrets)
        name = self.name(alias)
        cmd = ["podman", "run", "-d", "--name", name, "--network", self.network, "--network-alias", alias]
        cmd += env_args(env) + extra
        if container.get("command"):
            cmd += ["--entrypoint", container["command"][0]]
        cmd += [container["image"]] + container.get("command", [])[1:] + k8s_args(container, env)
        run(cmd)
        self.containers.append(name)
        self.hosts[alias] = run(["podman", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                                 name]).stdout.strip()

    def host_args(self) -> List[str]:
        return [arg for alias, ip in self.hosts.items() for arg in ("--add-host", f"{alias}:{ip}")]

    def backends(self) -> None:
        run(["podman", "network", "create", self.network])
        # Rootless podman cannot chown a fresh PGDATA volume; tmpfs instead of the PVC.
        self.start_backend("StatefulSet", "atlas-postgres", "atlas-postgres",
                           ["--tmpfs", "/var/lib/postgresql/data", "--tmpfs", "/var/run/postgresql"])
        self.start_backend("Deployment", "atlas-redis", "atlas-redis", [])
        self.start_backend("StatefulSet", "atlas-minio", "atlas-minio", ["--tmpfs", "/bitnami/minio/data"])

    def migrate_concurrently(self) -> None:
        init = self.api["spec"]["template"]["spec"]["initContainers"][0]
        env = container_env(init, self.configmap, self.secrets)
        procs = []
        for index in range(self.replicas):
            name = self.name(f"migrate-{index + 1}")
            cmd = ["podman", "run", "--rm", "--name", name, "--network", self.network] + self.host_args() + env_args(env)
            cmd += ["--entrypoint", init["command"][0], self.image] + init["command"][1:]
            procs.append(subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
        upgraded = 0
        for index, proc in enumerate(procs, start=1):
            out, _ = proc.communicate(timeout=600)
            if proc.returncode != 0:
                raise RuntimeError(f"migrate-{index} exited {proc.returncode}:\n{out}")
            upgraded += "Upgrading chat-history schema" in out
            print(f"  migrate-{index}: " + out.strip().splitlines()[-1].split(": ", 1)[-1])
        if upgraded != 1:
            raise RuntimeError(f"expected exactly one init container to apply the migration, got {upgraded}")
        print(f"PASS  migrations: {self.replicas} concurrent init containers, exactly one upgraded")

    def start_api(self, index: int) -> str:
        container = self.api["spec"]["template"]["spec"]["containers"][0]
        env = container_env(container, self.configmap, self.secrets)
        name = self.name(f"api-{index}")
        port = self.ports.setdefault(name, free_port())
        limits = container["resources"]["limits"]
        cmd = ["podman", "run", "-d", "--name", name, "--network", self.network,
               "-p", f"127.0.0.1:{port}:{container['ports'][0]['containerPort']}",
               "--memory", limits["memory"].replace("Gi", "g"), "--cpus", str(limits["cpu"]),
               "--tmpfs", "/tmp", "--tmpfs", "/data", "--tmpfs", "/app/logs", "--tmpfs", "/app/runtime",
               "--security-opt", "no-new-privileges", "--cap-drop", "ALL"] + self.host_args()
        cmd += env_args(env) + [self.image]
        run(cmd)
        if name not in self.containers:
            self.containers.append(name)
        return name

    def wait_started(self, name: str) -> None:
        probe = self.api["spec"]["template"]["spec"]["containers"][0]["startupProbe"]
        budget = probe["periodSeconds"] * probe["failureThreshold"]
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            if run(["podman", "exec", name] + probe["exec"]["command"], check=False).returncode == 0:
                return
            state = run(["podman", "inspect", "-f", "{{.State.Running}}", name], check=False).stdout.strip()
            if state != "true":
                break
            time.sleep(probe["periodSeconds"])
        logs = run(["podman", "logs", "--tail", "40", name], check=False)
        raise RuntimeError(f"{name} did not pass its startup probe:\n{logs.stdout}{logs.stderr}")

    def urls(self, names: List[str]) -> List[str]:
        return [f"--replica={n.rsplit('-', 1)[0]}=http://127.0.0.1:{self.ports[n]}" for n in names]

    def validator(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, ATLAS_PROXY_SECRET=self.secrets["PROXY_SECRET"])
        return subprocess.run([sys.executable, str(VALIDATOR), *args], env=env, text=True, capture_output=True)

    def failover(self, names: List[str]) -> None:
        """Terminate a replica under a live socket; the client resumes elsewhere."""
        user = f"drill-{uuid.uuid4().hex[:8]}@example.com"
        env = dict(os.environ, ATLAS_PROXY_SECRET=self.secrets["PROXY_SECRET"])
        holder = subprocess.Popen(
            [sys.executable, str(VALIDATOR), "hold-session", *self.urls(names[:1]), f"--user={user}"],
            env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        session_id = holder.stdout.readline().strip()
        if not session_id or " " in session_id:
            holder.kill()
            raise RuntimeError(f"hold-session failed: {session_id}{holder.stdout.read()}")
        grace = self.api["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]
        started = time.monotonic()
        run(["podman", "stop", "-t", str(grace), names[0]])
        stopped_in = time.monotonic() - started
        closed = holder.communicate(timeout=30)[0].strip()
        if holder.returncode != 0 or not closed.startswith("closed by server"):
            raise RuntimeError(f"the live socket did not see a server close: {closed}")
        resumed = self.validator("resume-session", *self.urls(names[1:2]), f"--user={user}",
                                 f"--session-id={session_id}")
        if resumed.returncode != 0:
            raise RuntimeError(f"session not resumed after {names[0]} stopped:\n{resumed.stdout}{resumed.stderr}")
        print(f"PASS  failover: {names[0]} stopped by SIGTERM in {stopped_in:.1f}s with a live socket "
              f"({closed}); session {session_id[:8]} resumed on {names[1]}")
        run(["podman", "rm", names[0]])
        self.start_api(int(names[0].rsplit("-", 2)[-2]))
        self.wait_started(names[0])
        print(f"PASS  restart: {names[0]} replaced and passed its startup probe")

    def cleanup(self) -> None:
        if self.keep:
            print(f"kept containers {self.containers} on network {self.network}")
            return
        for name in reversed(self.containers):
            run(["podman", "rm", "-f", "-t", "0", name], check=False)
        run(["podman", "network", "rm", "-f", self.network], check=False)

    def main(self) -> int:
        try:
            print(f"Rendered {len(self.docs)} objects from {OVERLAY.relative_to(HERE.parents[1])}")
            self.backends()
            self.migrate_concurrently()
            names = [self.start_api(i) for i in range(1, self.replicas + 1)]
            for name in names:
                self.wait_started(name)
            print(f"PASS  startup: {len(names)} API containers passed the manifest's startup probe")
            result = self.validator("all", *self.urls(names))
            print(result.stdout + result.stderr, end="")
            if result.returncode != 0:
                return 1
            self.failover(names)
            result = self.validator("all", *self.urls(names))
            print(result.stdout + result.stderr, end="")
            return result.returncode
        finally:
            self.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True, help="Atlas image to run, e.g. localhost/atlas-ui-3:dev")
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--keep", action="store_true", help="Leave the containers running afterwards")
    args = parser.parse_args()
    for tool in ("podman", "kubectl"):
        if shutil.which(tool) is None:
            print(f"{tool} is required", file=sys.stderr)
            return 2
    if args.replicas < 2:
        print("--replicas must be at least 2", file=sys.stderr)
        return 2
    return Rehearsal(args.image, args.replicas, args.keep).main()


if __name__ == "__main__":
    sys.exit(main())
