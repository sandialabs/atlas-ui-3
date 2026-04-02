"""Persistent agent state storage using PostgreSQL.

Uses the existing Prefect PostgreSQL instance with a dedicated 'atlas_agents' table.
Falls back to in-memory storage when the database is unavailable.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

def _to_dt(val) -> Optional[datetime]:
    """Coerce a value to a datetime for asyncpg TIMESTAMPTZ columns."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return None
    return None

logger = logging.getLogger(__name__)

# Reuse the Prefect Postgres connection
AGENT_DB_URL = os.getenv(
    "AGENT_DB_URL",
    "postgresql://prefect:prefect-secret-password@prefect-postgres:5432/prefect",
)

# SQL for table creation (idempotent)
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS atlas_agents (
    id VARCHAR(12) PRIMARY KEY,
    template_id VARCHAR(64) NOT NULL,
    name VARCHAR(256) NOT NULL,
    description TEXT,
    owner VARCHAR(256) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    mcp_servers JSONB DEFAULT '[]',
    max_steps INTEGER DEFAULT 10,
    loop_strategy VARCHAR(32) DEFAULT 'think-act',
    sandbox_policy VARCHAR(32) DEFAULT 'restrictive',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    stopped_by VARCHAR(256),
    last_activity TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    steps_completed INTEGER DEFAULT 0,
    prefect_data JSONB,
    has_token BOOLEAN DEFAULT FALSE,
    environment JSONB DEFAULT '{}',
    actions_log JSONB DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_atlas_agents_owner ON atlas_agents(owner);
CREATE INDEX IF NOT EXISTS idx_atlas_agents_status ON atlas_agents(status);
CREATE INDEX IF NOT EXISTS idx_atlas_agents_template ON atlas_agents(template_id);
"""


class AgentStore:
    """Persistent agent storage backed by PostgreSQL.

    Uses asyncpg for async database access. Falls back to in-memory
    dict when the database connection fails.
    """

    def __init__(self):
        self._pool = None
        self._available: Optional[bool] = None
        self._memory_fallback: Dict[str, Dict[str, Any]] = {}

    async def initialize(self) -> bool:
        """Initialize the database connection and create the table."""
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(AGENT_DB_URL, min_size=1, max_size=5)
            async with self._pool.acquire() as conn:
                await conn.execute(_CREATE_TABLE_SQL)
            self._available = True
            logger.info("Agent store initialized with PostgreSQL at %s", AGENT_DB_URL.split("@")[1] if "@" in AGENT_DB_URL else AGENT_DB_URL)
            return True
        except ImportError:
            logger.warning("asyncpg not installed; agent store using in-memory fallback")
            self._available = False
            return False
        except Exception as exc:
            logger.warning("Agent store DB init failed: %s; using in-memory fallback", exc)
            self._available = False
            return False

    async def save(self, agent: Dict[str, Any]) -> bool:
        """Save or update an agent record."""
        if not self._available or not self._pool:
            self._memory_fallback[agent["id"]] = agent
            return True

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO atlas_agents (
                        id, template_id, name, description, owner, status,
                        mcp_servers, max_steps, loop_strategy, sandbox_policy,
                        created_at, started_at, stopped_at, stopped_by,
                        last_activity, steps_completed, prefect_data,
                        has_token, environment, actions_log
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        stopped_at = EXCLUDED.stopped_at,
                        stopped_by = EXCLUDED.stopped_by,
                        last_activity = EXCLUDED.last_activity,
                        steps_completed = EXCLUDED.steps_completed,
                        prefect_data = EXCLUDED.prefect_data,
                        has_token = EXCLUDED.has_token,
                        actions_log = EXCLUDED.actions_log
                    """,
                    agent["id"],
                    agent.get("template_id", "custom"),
                    agent.get("name", ""),
                    agent.get("description", ""),
                    agent.get("owner", ""),
                    agent.get("status", "running"),
                    json.dumps(agent.get("mcp_servers", [])),
                    agent.get("max_steps", 10),
                    agent.get("loop_strategy", "think-act"),
                    agent.get("sandbox_policy", "restrictive"),
                    _to_dt(agent.get("created_at")) or datetime.now(timezone.utc),
                    _to_dt(agent.get("started_at")),
                    _to_dt(agent.get("stopped_at")),
                    agent.get("stopped_by"),
                    _to_dt(agent.get("last_activity")) or datetime.now(timezone.utc),
                    agent.get("steps_completed", 0),
                    json.dumps(agent.get("prefect")) if agent.get("prefect") else None,
                    agent.get("has_token", False),
                    json.dumps(agent.get("environment", {})),
                    json.dumps(agent.get("actions_log", [])),
                )
            return True
        except Exception as exc:
            logger.error("Failed to save agent %s: %s", agent["id"], exc)
            self._memory_fallback[agent["id"]] = agent
            return False

    async def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get an agent by ID."""
        if not self._available or not self._pool:
            return self._memory_fallback.get(agent_id)

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM atlas_agents WHERE id = $1", agent_id
                )
                if row:
                    return self._row_to_dict(row)
                return self._memory_fallback.get(agent_id)
        except Exception as exc:
            logger.error("Failed to get agent %s: %s", agent_id, exc)
            return self._memory_fallback.get(agent_id)

    async def list_agents(
        self,
        owner: Optional[str] = None,
        is_admin: bool = False,
    ) -> List[Dict[str, Any]]:
        """List agents, optionally filtered by owner."""
        if not self._available or not self._pool:
            agents = list(self._memory_fallback.values())
            if not is_admin and owner:
                agents = [a for a in agents if a.get("owner") == owner]
            return agents

        try:
            async with self._pool.acquire() as conn:
                if is_admin:
                    rows = await conn.fetch(
                        "SELECT * FROM atlas_agents ORDER BY created_at DESC LIMIT 100"
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM atlas_agents WHERE owner = $1 ORDER BY created_at DESC LIMIT 100",
                        owner,
                    )
                return [self._row_to_dict(r) for r in rows]
        except Exception as exc:
            logger.error("Failed to list agents: %s", exc)
            agents = list(self._memory_fallback.values())
            if not is_admin and owner:
                agents = [a for a in agents if a.get("owner") == owner]
            return agents

    async def delete(self, agent_id: str) -> bool:
        """Delete an agent record."""
        self._memory_fallback.pop(agent_id, None)

        if not self._available or not self._pool:
            return True

        try:
            async with self._pool.acquire() as conn:
                await conn.execute("DELETE FROM atlas_agents WHERE id = $1", agent_id)
            return True
        except Exception as exc:
            logger.error("Failed to delete agent %s: %s", agent_id, exc)
            return False

    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert an asyncpg Record to a dict matching the agent schema."""
        d = dict(row)
        # Convert JSONB columns from strings if needed
        for field in ("mcp_servers", "environment", "actions_log"):
            if isinstance(d.get(field), str):
                d[field] = json.loads(d[field])
        if isinstance(d.get("prefect_data"), str):
            d["prefect"] = json.loads(d["prefect_data"])
        elif d.get("prefect_data"):
            d["prefect"] = d["prefect_data"]
        else:
            d["prefect"] = None
        d.pop("prefect_data", None)

        # Convert datetime fields to ISO strings
        for field in ("created_at", "started_at", "stopped_at", "last_activity"):
            if d.get(field) and hasattr(d[field], "isoformat"):
                d[field] = d[field].isoformat()

        return d


# Module singleton
_agent_store: Optional[AgentStore] = None


async def get_agent_store() -> AgentStore:
    """Get or create the module-level AgentStore singleton."""
    global _agent_store
    if _agent_store is None:
        _agent_store = AgentStore()
        await _agent_store.initialize()
    return _agent_store
