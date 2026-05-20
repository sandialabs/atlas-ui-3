"""Thin async wrapper around the kubernetes python client for Agent Portal V3.

Loads kubeconfig (in-cluster -> ~/.kube/config -> KUBECONFIG env) lazily
on first use so the import doesn't fail if k8s isn't reachable. All
network calls happen in a worker thread via asyncio.to_thread so they
don't block the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

from kubernetes import client, config, watch
from kubernetes.client.exceptions import ApiException

logger = logging.getLogger(__name__)

_loaded = False


def _ensure_config() -> None:
    """Load kubeconfig once. Prefers in-cluster, falls back to local kubeconfig."""
    global _loaded
    if _loaded:
        return
    try:
        config.load_incluster_config()
        logger.info("agent_portal_v3: loaded in-cluster kube config")
    except config.ConfigException:
        # Some hosts set KUBECONFIG to a path the running user can't read
        # (e.g. /etc/rancher/k3s/k3s.yaml). Force the user's ~/.kube/config
        # when available and the env var points at an unreadable file.
        env_path = os.environ.get("KUBECONFIG", "")
        user_path = os.path.expanduser("~/.kube/config")
        path: Optional[str] = None
        if env_path and os.access(env_path, os.R_OK):
            path = env_path
        elif os.access(user_path, os.R_OK):
            path = user_path
        if path:
            config.load_kube_config(config_file=path)
            logger.info("agent_portal_v3: loaded kube config from %s", path)
        else:
            # last resort: default lookup (may raise)
            config.load_kube_config()
            logger.info("agent_portal_v3: loaded kube config from default search")
    _loaded = True


def _batch_api() -> client.BatchV1Api:
    _ensure_config()
    return client.BatchV1Api()


def _core_api() -> client.CoreV1Api:
    _ensure_config()
    return client.CoreV1Api()


def _networking_api() -> client.NetworkingV1Api:
    _ensure_config()
    return client.NetworkingV1Api()


class K8sError(RuntimeError):
    pass


# ---- Jobs ----

async def create_job(namespace: str, body: Dict[str, Any]) -> Dict[str, Any]:
    def _do() -> Any:
        try:
            return _batch_api().create_namespaced_job(namespace=namespace, body=body)
        except ApiException as e:
            raise K8sError(f"create_job failed: {e.status} {e.reason}: {e.body}") from e
    obj = await asyncio.to_thread(_do)
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


async def delete_job(namespace: str, name: str) -> None:
    def _do() -> None:
        try:
            _batch_api().delete_namespaced_job(
                name=name,
                namespace=namespace,
                propagation_policy="Background",
            )
        except ApiException as e:
            if e.status == 404:
                return
            raise K8sError(f"delete_job failed: {e.status} {e.reason}") from e
    await asyncio.to_thread(_do)


async def get_job(namespace: str, name: str) -> Optional[Dict[str, Any]]:
    def _do() -> Optional[Any]:
        try:
            return _batch_api().read_namespaced_job(name=name, namespace=namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise K8sError(f"get_job failed: {e.status} {e.reason}") from e
    obj = await asyncio.to_thread(_do)
    if obj is None:
        return None
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


# ---- Pods ----

async def list_pods_for_job(namespace: str, job_name: str) -> List[Dict[str, Any]]:
    def _do() -> List[Any]:
        try:
            res = _core_api().list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}",
            )
            return list(res.items)
        except ApiException as e:
            raise K8sError(f"list_pods failed: {e.status} {e.reason}") from e
    items = await asyncio.to_thread(_do)
    return [i.to_dict() for i in items]


async def get_pod_logs(
    namespace: str,
    pod_name: str,
    *,
    tail_lines: Optional[int] = None,
    container: Optional[str] = None,
) -> str:
    def _do() -> str:
        try:
            kwargs: Dict[str, Any] = {"name": pod_name, "namespace": namespace}
            if tail_lines is not None:
                kwargs["tail_lines"] = tail_lines
            if container is not None:
                kwargs["container"] = container
            return _core_api().read_namespaced_pod_log(**kwargs)
        except ApiException as e:
            if e.status == 404:
                return ""
            if e.status == 400:
                # pod not ready / no logs yet
                return ""
            raise K8sError(f"get_pod_logs failed: {e.status} {e.reason}") from e
    return await asyncio.to_thread(_do)


async def stream_pod_logs(
    namespace: str,
    pod_name: str,
    *,
    container: Optional[str] = None,
) -> AsyncIterator[str]:
    """Yields log chunks as they arrive. Caller is responsible for cancelling."""
    queue: "asyncio.Queue[Optional[str]]" = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _producer() -> None:
        try:
            w = watch.Watch()
            kwargs: Dict[str, Any] = {
                "name": pod_name,
                "namespace": namespace,
                "follow": True,
                "_preload_content": False,
            }
            if container is not None:
                kwargs["container"] = container
            stream = _core_api().read_namespaced_pod_log(**kwargs)
            for line in stream.stream(decode_content=True):
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                asyncio.run_coroutine_threadsafe(queue.put(line), loop)
        except Exception as e:  # noqa: BLE001
            asyncio.run_coroutine_threadsafe(
                queue.put(f"[stream error: {e}]"), loop
            )
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop)

    task = asyncio.to_thread(_producer)
    asyncio.create_task(task)

    while True:
        chunk = await queue.get()
        if chunk is None:
            return
        yield chunk


# ---- NetworkPolicies ----

async def upsert_network_policy(namespace: str, body: Dict[str, Any]) -> None:
    name = body.get("metadata", {}).get("name")

    def _do() -> None:
        api = _networking_api()
        try:
            api.read_namespaced_network_policy(name=name, namespace=namespace)
        except ApiException as e:
            if e.status == 404:
                try:
                    api.create_namespaced_network_policy(namespace=namespace, body=body)
                except ApiException as ce:
                    raise K8sError(
                        f"create_network_policy failed: {ce.status} {ce.reason}"
                    ) from ce
                return
            raise K8sError(f"read_network_policy failed: {e.status} {e.reason}") from e
        # exists - replace
        try:
            api.replace_namespaced_network_policy(
                name=name, namespace=namespace, body=body
            )
        except ApiException as re:
            raise K8sError(
                f"replace_network_policy failed: {re.status} {re.reason}"
            ) from re

    await asyncio.to_thread(_do)


# ---- Cluster connectivity ----

async def cluster_reachable() -> bool:
    def _do() -> bool:
        try:
            _ensure_config()
            client.CoreV1Api().get_api_resources()
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("agent_portal_v3: cluster not reachable: %s", e)
            return False
    return await asyncio.to_thread(_do)
