"""Per-session workspace lifecycle and reaper.

Each FastMCP session gets a workspace under ``workspaces_dir/<session_id>/``.
The directory is wiped on:

* explicit ``reset_session()``
* idle TTL exceeded (background reaper)
* server shutdown (graceful)

The session_id is derived from the FastMCP context. The session_state
store gives us per-session persistence of metadata across tool calls.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


logger = logging.getLogger(__name__)


@dataclass
class SessionRecord:
    session_id: str
    workspace: Path
    created_at: float
    last_used: float
    last_seen_mtimes: Dict[str, float] = field(default_factory=dict)


class SessionRegistry:
    """In-process registry of live sessions.

    Each MCP HTTP session calls ``get_or_create`` on first tool use; the
    record is updated on every subsequent call. The reaper task walks
    the registry periodically and evicts idle entries.
    """

    def __init__(
        self,
        workspaces_dir: Path,
        *,
        ttl_s: int,
        max_sessions: int,
        reaper_interval_s: int = 300,
    ) -> None:
        self._root = Path(workspaces_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl_s = ttl_s
        self._max_sessions = max_sessions
        self._reaper_interval_s = reaper_interval_s
        self._sessions: Dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None
        self._stopped = False

    async def start(self) -> None:
        """Wipe any stale workspaces from a prior process and start the reaper."""
        for child in self._root.iterdir() if self._root.exists() else []:
            if child.is_dir():
                try:
                    shutil.rmtree(child)
                except OSError as e:
                    logger.warning("failed to wipe stale workspace %s: %s", child, e)
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(), name="code-executor-v2-reaper"
        )

    async def stop(self) -> None:
        self._stopped = True
        if self._reaper_task:
            self._reaper_task.cancel()
            try:
                await self._reaper_task
            except asyncio.CancelledError:
                # Expected: we just cancelled it.
                pass
            except Exception as e:
                # Reaper failure during shutdown is logged but not fatal —
                # we still need to wipe the workspaces below.
                logger.warning("reaper raised during shutdown: %s", e)
        async with self._lock:
            for record in list(self._sessions.values()):
                self._destroy_record(record)
            self._sessions.clear()

    async def get_or_create(self, session_id: Optional[str]) -> SessionRecord:
        sid = session_id or str(uuid.uuid4())
        async with self._lock:
            record = self._sessions.get(sid)
            now = time.time()
            if record is not None:
                record.last_used = now
                return record
            if len(self._sessions) >= self._max_sessions:
                raise RuntimeError(
                    f"max sessions reached ({self._max_sessions}); "
                    "wait for idle TTL or call reset"
                )
            workspace = self._root / sid
            workspace.mkdir(parents=True, exist_ok=True)
            record = SessionRecord(
                session_id=sid,
                workspace=workspace,
                created_at=now,
                last_used=now,
            )
            self._sessions[sid] = record
            logger.info("created session %s at %s", sid, workspace)
            return record

    async def reset(self, session_id: str) -> SessionRecord:
        async with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                raise KeyError(session_id)
            self._destroy_record(record)
            record.workspace.mkdir(parents=True, exist_ok=True)
            record.last_seen_mtimes = {}
            record.last_used = time.time()
            return record

    async def destroy(self, session_id: str) -> None:
        async with self._lock:
            record = self._sessions.pop(session_id, None)
            if record is not None:
                self._destroy_record(record)

    def _destroy_record(self, record: SessionRecord) -> None:
        try:
            if record.workspace.exists():
                shutil.rmtree(record.workspace)
        except OSError as e:
            logger.warning(
                "failed to wipe workspace %s: %s", record.workspace, e
            )

    async def _reaper_loop(self) -> None:
        while not self._stopped:
            try:
                await asyncio.sleep(self._reaper_interval_s)
            except asyncio.CancelledError:
                return
            cutoff = time.time() - self._ttl_s
            async with self._lock:
                expired = [
                    sid for sid, rec in self._sessions.items()
                    if rec.last_used < cutoff
                ]
                for sid in expired:
                    rec = self._sessions.pop(sid)
                    logger.info(
                        "reaping idle session %s (idle %.0fs)",
                        sid, time.time() - rec.last_used,
                    )
                    self._destroy_record(rec)

    def stats(self) -> Dict[str, object]:
        return {
            "live_sessions": len(self._sessions),
            "max_sessions": self._max_sessions,
            "ttl_s": self._ttl_s,
            "workspaces_root": str(self._root),
        }
