"""Orchestrator that ties the store, k8s client, and job templates together.

Responsibilities:
- launch_run: create DB record + K8s Job + NetworkPolicy
- cancel_run: delete Job (pod cleanup follows via propagation policy)
- delete_run: cancel + remove DB record
- get_run_logs: fetch pod logs (no streaming yet -- routes can call repeatedly)
- start_watcher: background task that polls active Jobs and updates the DB
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import k8s_client
from .job_template import (
    build_job_manifest,
    build_network_policy,
)
from .models import (
    ACTIVE_STATUSES,
    RUN_STATUS_CANCELLED,
    RUN_STATUS_FAILED,
    RUN_STATUS_LAUNCHING,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCEEDED,
    AgentRunRecord,
)
from .store import AgentRunStore, RunNotFoundError, get_agent_run_store

logger = logging.getLogger(__name__)


class RunnerError(RuntimeError):
    pass


# Default agent image. Override via AGENT_PORTAL_V3_IMAGE env var.
DEFAULT_AGENT_IMAGE = "localhost/atlas-agent-runner:dev"


def serialize_run(record: AgentRunRecord, *, include_prompt: bool = True) -> Dict[str, Any]:
    return {
        "id": record.id,
        "user_email": record.user_email,
        "display_name": record.display_name,
        "prompt": record.prompt if include_prompt else None,
        "mcp_servers": json.loads(record.mcp_servers_json or "[]"),
        "llm_provider": record.llm_provider,
        "llm_model": record.llm_model,
        "status": record.status,
        "exit_code": record.exit_code,
        "error": record.error,
        "namespace": record.namespace,
        "job_name": record.job_name,
        "pod_name": record.pod_name,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


class AgentRunner:
    def __init__(
        self,
        *,
        store: Optional[AgentRunStore] = None,
        default_namespace: str = "atlas",
        agent_image: Optional[str] = None,
        active_deadline_seconds: int = 1800,
    ) -> None:
        self._store = store or get_agent_run_store()
        self._namespace = default_namespace
        self._image = (
            agent_image
            or os.environ.get("AGENT_PORTAL_V3_IMAGE")
            or DEFAULT_AGENT_IMAGE
        )
        self._active_deadline_seconds = active_deadline_seconds
        self._watcher_task: Optional[asyncio.Task] = None
        self._llm_keys: Dict[str, str] = {}

    @property
    def namespace(self) -> str:
        return self._namespace

    @property
    def image(self) -> str:
        return self._image

    def set_llm_keys(self, keys: Dict[str, str]) -> None:
        """Stash API keys discovered from atlas settings so launches can
        forward the right one to the pod. Mapping is provider -> key value."""
        self._llm_keys = dict(keys)

    # ---- launch / cancel / delete ----

    async def launch_run(
        self,
        *,
        user_email: str,
        prompt: str,
        mcp_servers: List[str],
        mcp_resolved: Dict[str, Any],
        llm_provider: str,
        llm_model: str,
        display_name: str = "",
        extra_env: Optional[Dict[str, str]] = None,
        egress: Optional[Any] = None,
    ) -> AgentRunRecord:
        record = self._store.create_run(
            user_email=user_email,
            display_name=display_name,
            prompt=prompt,
            mcp_servers=mcp_servers,
            mcp_resolved=mcp_resolved,
            llm_provider=llm_provider,
            llm_model=llm_model,
            namespace=self._namespace,
        )
        try:
            api_key = self._llm_keys.get(llm_provider.lower())
            manifest = build_job_manifest(
                run_id=record.id,
                user_email=user_email,
                display_name=display_name or llm_model,
                namespace=self._namespace,
                image=self._image,
                prompt=prompt,
                mcp_resolved=mcp_resolved,
                llm_provider=llm_provider,
                llm_model=llm_model,
                llm_api_key_inline=api_key,
                extra_env=extra_env,
                active_deadline_seconds=self._active_deadline_seconds,
            )
            deny_by_default = bool(getattr(egress, "deny_by_default", False))
            allow_cidrs = list(getattr(egress, "cidrs", []) or [])
            netpol = build_network_policy(
                run_id=record.id,
                namespace=self._namespace,
                llm_provider=llm_provider,
                mcp_resolved=mcp_resolved,
                deny_by_default=deny_by_default,
                allow_cidrs=allow_cidrs,
            )
            if deny_by_default:
                domains = getattr(egress, "domains", []) or []
                unresolved = getattr(egress, "unresolved", []) or []
                self._store.append_event(
                    record.id,
                    "system",
                    f"Egress allowlist ({getattr(egress, 'mode', '')}): "
                    f"{', '.join(domains) or '(none)'}"
                    + (f"  [unenforceable in Phase 0: {', '.join(unresolved)}]" if unresolved else ""),
                )

            # NetworkPolicy first (best-effort -- not all clusters enforce them).
            try:
                await k8s_client.upsert_network_policy(self._namespace, netpol)
            except Exception as e:  # noqa: BLE001
                logger.warning("network policy apply failed (continuing): %s", e)
                self._store.append_event(
                    record.id,
                    "system",
                    f"NetworkPolicy apply failed (non-fatal): {e}",
                )

            await k8s_client.create_job(self._namespace, manifest)
            self._store.append_event(
                record.id,
                "system",
                f"Job {manifest['metadata']['name']} submitted to namespace "
                f"{self._namespace}",
            )
            return self._store.mark_status(
                record.id,
                RUN_STATUS_LAUNCHING,
                job_name=manifest["metadata"]["name"],
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("launch_run failed for %s", record.id)
            self._store.append_event(record.id, "system", f"Launch failed: {e}")
            return self._store.mark_status(
                record.id,
                RUN_STATUS_FAILED,
                error=str(e),
                finished_at=datetime.now(timezone.utc),
            )

    async def cancel_run(self, run_id: str, user_email: str) -> AgentRunRecord:
        record = self._store.get_run(run_id, user_email=user_email)
        if record.status not in ACTIVE_STATUSES:
            return record
        if record.job_name:
            try:
                await k8s_client.delete_job(record.namespace, record.job_name)
                self._store.append_event(run_id, "system", "Job deleted by user")
            except Exception as e:  # noqa: BLE001
                logger.warning("cancel: delete_job error: %s", e)
                self._store.append_event(run_id, "system", f"delete_job error: {e}")
        return self._store.mark_status(
            run_id,
            RUN_STATUS_CANCELLED,
            finished_at=datetime.now(timezone.utc),
        )

    async def delete_run(self, run_id: str, user_email: str) -> bool:
        try:
            record = self._store.get_run(run_id, user_email=user_email)
        except RunNotFoundError:
            return False
        if record.status in ACTIVE_STATUSES and record.job_name:
            try:
                await k8s_client.delete_job(record.namespace, record.job_name)
            except Exception as e:  # noqa: BLE001
                logger.warning("delete: delete_job error: %s", e)
        return self._store.delete_run(run_id, user_email)

    # ---- reads ----

    def list_runs(self, user_email: str, *, limit: int = 100) -> List[AgentRunRecord]:
        return self._store.list_runs(user_email, limit=limit)

    def get_run(self, run_id: str, user_email: str) -> AgentRunRecord:
        return self._store.get_run(run_id, user_email=user_email)

    def list_events(self, run_id: str, *, limit: int = 1000):
        return self._store.list_events(run_id, limit=limit)

    async def get_run_logs(
        self, run_id: str, user_email: str, *, tail_lines: Optional[int] = 1000
    ) -> str:
        record = self._store.get_run(run_id, user_email=user_email)
        if not record.pod_name:
            # try to discover
            await self._refresh_run(record)
            record = self._store.get_run(run_id, user_email=user_email)
        if not record.pod_name:
            return ""
        return await k8s_client.get_pod_logs(
            record.namespace, record.pod_name, tail_lines=tail_lines
        )

    # ---- watcher ----

    def start_watcher(self, interval_seconds: int = 5) -> None:
        if self._watcher_task and not self._watcher_task.done():
            return
        self._watcher_task = asyncio.create_task(
            self._watch_loop(interval_seconds), name="agent_portal_v3_watcher"
        )

    async def stop_watcher(self) -> None:
        if not self._watcher_task:
            return
        self._watcher_task.cancel()
        try:
            await self._watcher_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._watcher_task = None

    async def _watch_loop(self, interval_seconds: int) -> None:
        logger.info("agent_portal_v3 watcher started")
        try:
            while True:
                try:
                    active = self._store.list_active_runs()
                    if active:
                        # bound concurrency a little
                        await asyncio.gather(
                            *(self._refresh_run(r) for r in active),
                            return_exceptions=True,
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("watcher tick failed")
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("agent_portal_v3 watcher stopped")
            raise

    async def _refresh_run(self, record: AgentRunRecord) -> None:
        if not record.job_name:
            return
        try:
            job = await k8s_client.get_job(record.namespace, record.job_name)
        except Exception as e:  # noqa: BLE001
            logger.warning("refresh: get_job failed: %s", e)
            return
        if job is None:
            # job vanished -- mark unknown -> failed for safety
            self._store.mark_status(
                record.id,
                RUN_STATUS_FAILED,
                error="Job no longer exists in cluster",
                finished_at=datetime.now(timezone.utc),
            )
            return

        status = job.get("status", {}) or {}
        active = status.get("active") or 0
        succeeded = status.get("succeeded") or 0
        failed = status.get("failed") or 0
        start_time = status.get("start_time")

        new_status: Optional[str] = None
        update_fields: Dict[str, Any] = {}

        if succeeded:
            new_status = RUN_STATUS_SUCCEEDED
            update_fields["finished_at"] = datetime.now(timezone.utc)
            update_fields["exit_code"] = 0
        elif failed:
            new_status = RUN_STATUS_FAILED
            update_fields["finished_at"] = datetime.now(timezone.utc)
            update_fields["exit_code"] = 1
        elif active:
            new_status = RUN_STATUS_RUNNING
            if start_time and not record.started_at:
                update_fields["started_at"] = (
                    start_time if isinstance(start_time, datetime) else None
                )

        # Pod name discovery
        try:
            pods = await k8s_client.list_pods_for_job(
                record.namespace, record.job_name
            )
            if pods:
                pod = pods[0]
                pod_name = pod.get("metadata", {}).get("name")
                if pod_name and pod_name != record.pod_name:
                    update_fields["pod_name"] = pod_name
        except Exception as e:  # noqa: BLE001
            logger.debug("refresh: list_pods failed: %s", e)

        if new_status and new_status != record.status:
            update_fields["status"] = new_status
            try:
                self._store.append_event(
                    record.id,
                    "status",
                    f"{record.status} -> {new_status}",
                )
            except Exception:  # noqa: BLE001
                pass

        if update_fields:
            try:
                self._store.update_run(run_id=record.id, **update_fields)
            except Exception as e:  # noqa: BLE001
                logger.warning("refresh: update failed: %s", e)


_runner: Optional[AgentRunner] = None


def get_agent_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner


def reset_runner() -> None:
    global _runner
    _runner = None
