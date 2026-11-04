#!/usr/bin/env python3
"""Offline checks of the Kubernetes/OpenShift staging manifests (issue #1129).

Renders deploy/kubernetes through staging.sh with ``kubectl kustomize`` (no
cluster is contacted) and asserts the deployment properties the staging
environment depends on: three replicas with no affinity or sticky sessions,
probes, resource limits, the lock-serialized migration init container,
shared Redis/PostgreSQL/S3 configuration, connection budgets, no committed
credentials, and the OpenShift variant's SCC compatibility.

Run by test/k8s_render_checks.sh (CI, on the host: the hermetic test image has
no kubectl). Needs python3 with PyYAML and kubectl.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "kubernetes"
IMAGE = "registry.example.com/atlas/atlas-ui-3:v0.0.0-render-check"
WORKLOAD_KINDS = ("Deployment", "StatefulSet")

results: List[tuple] = []


def check(name: str) -> Callable:
    def wrap(fn: Callable) -> Callable:
        try:
            fn()
            results.append((name, True, ""))
            print(f"PASSED: {name}")
        except (AssertionError, KeyError, IndexError, ValueError, TypeError, StopIteration) as exc:
            # A missing field is a failed check, not a crash of the whole run.
            results.append((name, False, repr(exc)))
            print(f"FAILED: {name}: {exc!r}")
        return fn
    return wrap


class Tree:
    """A scratch copy of deploy/kubernetes with throwaway secrets."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="k8s-render-"))
        self.dir = self.tmp / "kubernetes"
        shutil.copytree(DEPLOY, self.dir, ignore=shutil.ignore_patterns("secrets.env", ".build-*", "__pycache__"))
        self.secrets = self.dir / "overlays" / "staging" / "secrets.env"

    def write_secrets(self) -> Dict[str, str]:
        example = (self.dir / "overlays" / "staging" / "secrets.env.example").read_text()
        values = {}
        for line in example.splitlines():
            if line and not line.startswith("#"):
                key, _, value = line.partition("=")
                values[key] = value.replace("replace-me", "render-check-" + key.lower())
        self.secrets.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
        return values

    def staging_sh(self, *args: str, env: Dict[str, str] | None = None) -> subprocess.CompletedProcess:
        full_env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
        full_env.update(env or {})
        return subprocess.run(["bash", str(self.dir / "staging.sh"), *args], env=full_env,
                              text=True, capture_output=True)

    def render(self, flavor: str = "kubernetes", **env: str) -> List[dict]:
        result = self.staging_sh("render", env={"ATLAS_IMAGE": IMAGE, "ATLAS_FLAVOR": flavor, **env})
        assert result.returncode == 0, f"render ({flavor}) failed: {result.stderr}"
        return [doc for doc in yaml.safe_load_all(result.stdout) if doc]

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def get(docs: List[dict], kind: str, name: str) -> dict:
    """The rendered object; generated ConfigMaps and Secrets carry a hash suffix."""
    for doc in docs:
        actual = doc["metadata"]["name"]
        if doc["kind"] == kind and (actual == name or (kind in ("ConfigMap", "Secret") and actual.startswith(name + "-"))):
            return doc
    raise AssertionError(f"{kind}/{name} missing from the render")


def pod_spec(doc: dict) -> dict:
    return doc["spec"]["template"]["spec"]


def all_containers(spec: dict) -> List[dict]:
    return spec.get("initContainers", []) + spec["containers"]


def run_checks() -> None:
    tree = Tree()
    try:
        _run(tree)
    finally:
        tree.close()


def _run(tree: Tree) -> None:
    @check("render fails closed without secrets.env")
    def _():
        result = tree.staging_sh("render", env={"ATLAS_IMAGE": IMAGE})
        assert result.returncode != 0 and "secrets.env" in result.stderr, result.stderr[-400:]

    secret_values = tree.write_secrets()
    docs = tree.render()
    openshift = tree.render("openshift")
    api = get(docs, "Deployment", "atlas-api")
    spec = pod_spec(api)
    app = spec["containers"][0]
    config = get(docs, "ConfigMap", "atlas-api-env")["data"]

    @check("staging.sh refuses an unpinned image, :latest, and a missing kube context")
    def _():
        for image in ("quay.io/x/atlas-ui-3", "quay.io/x/atlas-ui-3:latest", "localhost:5000/atlas-ui-3"):
            result = tree.staging_sh("render", env={"ATLAS_IMAGE": image})
            assert result.returncode != 0, f"accepted {image}"
        result = tree.staging_sh("status")
        assert result.returncode != 0 and "ATLAS_KUBE_CONTEXT" in result.stderr, result.stderr

    @check("three API replicas, pinned image in every Atlas container")
    def _():
        assert api["spec"]["replicas"] == 3, api["spec"]["replicas"]
        for container in all_containers(spec):
            assert container["image"] == IMAGE, container["image"]
        five = get(tree.render(ATLAS_REPLICAS="5"), "Deployment", "atlas-api")
        assert five["spec"]["replicas"] == 5

    @check("no affinity, spreading, node pinning or sticky sessions")
    def _():
        for field in ("affinity", "topologySpreadConstraints", "nodeSelector", "nodeName"):
            assert field not in spec, f"atlas-api pod sets {field}"
        service = get(docs, "Service", "atlas-api")
        assert service["spec"].get("sessionAffinity") == "None", service["spec"]

    @check("rolling update keeps every replica serving (maxUnavailable 0) and a PDB exists")
    def _():
        rolling = api["spec"]["strategy"]["rollingUpdate"]
        assert api["spec"]["strategy"]["type"] == "RollingUpdate"
        assert rolling["maxUnavailable"] == 0 and rolling["maxSurge"] == 1, rolling
        pdb = get(docs, "PodDisruptionBudget", "atlas-api")
        assert pdb["spec"]["maxUnavailable"] == 1

    @check("startup, readiness and liveness probes on the API")
    def _():
        startup = " ".join(app["startupProbe"]["exec"]["command"])
        for needle in ("/api/health", "chat_history", "session_store"):
            assert needle in startup, f"startup probe does not check {needle}"
        assert app["readinessProbe"]["httpGet"]["path"] == "/api/heartbeat"
        assert app["livenessProbe"]["httpGet"]["path"] == "/api/heartbeat"
        assert app["lifecycle"]["preStop"]["exec"]["command"][0] == "sleep"
        assert spec["terminationGracePeriodSeconds"] > int(app["lifecycle"]["preStop"]["exec"]["command"][1])

    @check("every container in every workload has requests and limits")
    def _():
        for doc in docs:
            if doc["kind"] not in WORKLOAD_KINDS:
                continue
            for container in all_containers(pod_spec(doc)):
                resources = container.get("resources", {})
                for section in ("requests", "limits"):
                    for key in ("cpu", "memory"):
                        assert key in resources.get(section, {}), \
                            f"{doc['metadata']['name']}/{container['name']} lacks {section}.{key}"

    @check("every workload runs non-root without privilege escalation and drops all capabilities")
    def _():
        for doc in docs:
            if doc["kind"] not in WORKLOAD_KINDS:
                continue
            pod = pod_spec(doc)
            assert pod["securityContext"].get("runAsNonRoot") is True, doc["metadata"]["name"]
            assert pod.get("automountServiceAccountToken") is False, doc["metadata"]["name"]
            for container in all_containers(pod):
                sc = container.get("securityContext", {})
                assert sc.get("allowPrivilegeEscalation") is False, container["name"]
                assert sc.get("capabilities", {}).get("drop") == ["ALL"], container["name"]

    @check("migrations run in a lock-serialized init container before the API")
    def _():
        init = spec["initContainers"]
        assert [c["name"] for c in init] == ["migrate"], init
        assert init[0]["command"] == ["python", "-m", "atlas.modules.chat_history.schema_migration"]
        env = {e["name"]: e for e in init[0]["env"]}
        assert env["ATLAS_ALEMBIC_INI"]["value"] == "/app/alembic.ini"
        assert "secretKeyRef" in env["CHAT_HISTORY_DB_URL"]["valueFrom"]
        dockerfile = (ROOT / "Dockerfile").read_text()
        assert "COPY alembic.ini /app/alembic.ini" in dockerfile and "COPY alembic/ /app/alembic/" in dockerfile
        assert "--extra migrations" in dockerfile
        assert not any(d["kind"] == "Job" for d in docs), "a separate migration Job would race the init containers"

    @check("shared state configured: Redis sessions, PostgreSQL history, real S3, production auth")
    def _():
        expected = {
            "SESSION_REPOSITORY_TYPE": "redis", "FEATURE_CHAT_HISTORY_ENABLED": "true",
            "USE_MOCK_S3": "false", "DEBUG_MODE": "false", "FEATURE_PROXY_SECRET_ENABLED": "true",
        }
        for key, value in expected.items():
            assert config.get(key) == value, f"{key}={config.get(key)!r}"
        assert config.get("REDIS_KEY_PREFIX") == "atlas-staging"
        assert secret_values["CHAT_HISTORY_DB_URL"].startswith("postgresql://")
        assert secret_values["REDIS_URL"].startswith("redis://")

    @check("connection budgets fit PostgreSQL max_connections and Redis maxclients")
    def _():
        replicas = api["spec"]["replicas"] + api["spec"]["strategy"]["rollingUpdate"]["maxSurge"]
        per_process = int(config["CHAT_HISTORY_DB_POOL_SIZE"]) + int(config["CHAT_HISTORY_DB_MAX_OVERFLOW"])
        postgres_args = pod_spec(get(docs, "StatefulSet", "atlas-postgres"))["containers"][0]["args"]
        max_connections = int(next(a for a in postgres_args if a.startswith("max_connections=")).split("=")[1])
        # One migration connection per starting pod, plus room for admin sessions.
        needed = replicas * per_process + replicas + 5
        assert needed <= max_connections, f"PostgreSQL needs {needed}, max_connections={max_connections}"
        redis_args = pod_spec(get(docs, "Deployment", "atlas-redis"))["containers"][0]["args"]
        maxclients = int(redis_args[redis_args.index("--maxclients") + 1])
        assert replicas * int(config["REDIS_MAX_CONNECTIONS"]) < maxclients

    @check("no credential is committed: every secret comes from the generated Secret")
    def _():
        example_keys = set(secret_values)
        secret_pattern = re.compile(r"PASSWORD|SECRET|_KEY$|_URL$")
        for doc in docs:
            if doc["kind"] not in WORKLOAD_KINDS:
                continue
            for container in all_containers(pod_spec(doc)):
                for item in container.get("env", []):
                    ref = item.get("valueFrom", {}).get("secretKeyRef")
                    if ref:
                        assert ref["key"] in example_keys, f"{ref['key']} missing from secrets.env.example"
                    elif secret_pattern.search(item["name"]) and item["name"] != "ATLAS_ALEMBIC_INI":
                        raise AssertionError(f"{container['name']} sets {item['name']} inline")
        leaked = example_keys & set(config)
        assert not leaked, f"ConfigMap holds secret keys {sorted(leaked)}"
        tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "deploy/kubernetes"],
                                 text=True, capture_output=True).stdout.split()
        assert not [p for p in tracked if p.endswith("/secrets.env")], "secrets.env is tracked"

    @check("data volumes outlive a plain teardown: StatefulSet claims only, named as staging.sh expects")
    def _():
        assert not [d for d in docs if d["kind"] == "PersistentVolumeClaim"], \
            "a standalone PVC would be deleted by `delete -k` (plain teardown)"
        claims = sorted(
            f"{t['metadata']['name']}-{d['metadata']['name']}-0"
            for d in docs if d["kind"] == "StatefulSet" for t in d["spec"].get("volumeClaimTemplates", [])
        )
        assert claims == ["data-atlas-minio-0", "data-atlas-postgres-0"], claims
        script = (DEPLOY / "staging.sh").read_text()
        assert "claims=(data-atlas-postgres-0 data-atlas-minio-0)" in script, "teardown --purge misses a claim"

    @check("OpenShift flavor pins no UID or fsGroup (restricted-v2 SCC assigns them)")
    def _():
        text = yaml.safe_dump_all(openshift)
        for field in ("runAsUser", "runAsGroup", "fsGroup"):
            assert field not in text, f"OpenShift render still sets {field}"
        assert len(openshift) == len(docs)


def main() -> int:
    for tool in ("kubectl",):
        if shutil.which(tool) is None:
            print(f"FAILED: {tool} is required to render the manifests")
            return 1
    run_checks()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
