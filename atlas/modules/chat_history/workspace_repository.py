"""Repository for workspace persistence (workspace switcher).

A workspace is a named bundle of chat-context selections -- active prompt, RAG
data sources, and MCP tools -- so a user can flip between contexts like
"Work" / "Home" / "Project A" without re-picking everything.

Mirrors the conventions in ``user_prompt_repository``: every public method
normalizes ``user_email`` at the entry point so mixed-case identities from
different SSO/proxy paths still hit the same rows, and referential integrity is
enforced here rather than via database FK constraints (DuckDB compatibility).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session, sessionmaker

from atlas.core.user_identity import normalize_user_email

from .models import UserWorkspaceRecord

logger = logging.getLogger(__name__)

# Selection keys are MCP keys ("server_tool"), RAG source names, or prompt keys.
# The caps below exist so a workspace stays a bookmark and not a data dump; they
# are far above any realistic selection count.
MAX_SELECTION_ITEMS = 500
MAX_SELECTION_KEY_LEN = 300

# The stored config is a closed shape: unknown keys are dropped rather than
# persisted, so the blob the UI reads back is always the schema below. Adding a
# field to a workspace is therefore a deliberate change here, not something a
# client can do by POSTing extra JSON.
_LIST_FIELDS = ("selected_tools", "selected_prompts", "selected_data_sources")

EMPTY_CONFIG: Dict[str, Any] = {
    "active_prompt_key": None,
    "selected_tools": [],
    "selected_prompts": [],
    "selected_data_sources": [],
    "rag_enabled": False,
}


def _normalize_key_list(value: Any) -> List[str]:
    """Coerce a selection list to deduped, capped, non-empty strings."""
    if not isinstance(value, list):
        return []
    seen = set()
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item or len(item) > MAX_SELECTION_KEY_LEN or item in seen:
            continue
        seen.add(item)
        out.append(item)
        if len(out) >= MAX_SELECTION_ITEMS:
            break
    return out


def normalize_config(config: Any) -> Dict[str, Any]:
    """Return a workspace config in the canonical shape.

    Anything unrecognized is discarded, so a hand-crafted or stale payload can
    never surface as an unexpected shape in the UI.
    """
    if not isinstance(config, dict):
        return dict(EMPTY_CONFIG)

    active_prompt_key = config.get("active_prompt_key")
    if not isinstance(active_prompt_key, str) or not active_prompt_key.strip():
        active_prompt_key = None
    elif len(active_prompt_key) > MAX_SELECTION_KEY_LEN:
        active_prompt_key = None

    normalized: Dict[str, Any] = {
        "active_prompt_key": active_prompt_key,
        "rag_enabled": bool(config.get("rag_enabled", False)),
    }
    for field in _LIST_FIELDS:
        normalized[field] = _normalize_key_list(config.get(field))
    return normalized


class WorkspaceRepository:
    """Handles CRUD for per-user workspaces."""

    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    def _get_session(self) -> Session:
        return self._session_factory()

    @staticmethod
    def _to_dict(record: UserWorkspaceRecord) -> Dict[str, Any]:
        try:
            config = json.loads(record.config_json) if record.config_json else {}
        except (TypeError, ValueError):
            # A row written by an older/broken path must not break the list
            # endpoint for every other workspace the user owns.
            logger.warning("Workspace %s has unreadable config_json", record.id)
            config = {}
        return {
            "id": record.id,
            "name": record.name,
            "description": record.description,
            "config": normalize_config(config),
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    def list_workspaces(self, user_email: str) -> List[Dict[str, Any]]:
        """Return all workspaces for a user, most recently updated first."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            records = (
                session.query(UserWorkspaceRecord)
                .filter(UserWorkspaceRecord.user_email == user_email)
                .order_by(desc(UserWorkspaceRecord.updated_at))
                .all()
            )
            return [self._to_dict(r) for r in records]

    def get_workspace(
        self, workspace_id: str, user_email: str
    ) -> Optional[Dict[str, Any]]:
        """Return a single workspace owned by the user, or None."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            record = self._find(session, workspace_id, user_email)
            return self._to_dict(record) if record else None

    @staticmethod
    def _find(
        session: Session, workspace_id: str, user_email: str
    ) -> Optional[UserWorkspaceRecord]:
        return (
            session.query(UserWorkspaceRecord)
            .filter(
                UserWorkspaceRecord.id == workspace_id,
                UserWorkspaceRecord.user_email == user_email,
            )
            .first()
        )

    def create_workspace(
        self,
        user_email: str,
        name: str,
        config: Any = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a workspace for the user and return it."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            record = UserWorkspaceRecord(
                user_email=user_email,
                name=name.strip(),
                description=description.strip() if description else None,
                config_json=json.dumps(normalize_config(config)),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            logger.info("Created workspace %s for %s", record.id, user_email)
            return self._to_dict(record)

    def update_workspace(
        self,
        workspace_id: str,
        user_email: str,
        name: Optional[str] = None,
        config: Any = None,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update a workspace owned by the user. Returns None if not found.

        ``config`` is replaced wholesale rather than merged: the caller always
        holds the full selection state, and a merge would make it impossible to
        clear a selection list.
        """
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            record = self._find(session, workspace_id, user_email)
            if not record:
                return None
            if name is not None:
                record.name = name.strip()
            if description is not None:
                record.description = description.strip() or None
            if config is not None:
                record.config_json = json.dumps(normalize_config(config))
            record.updated_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(record)
            return self._to_dict(record)

    def delete_workspace(self, workspace_id: str, user_email: str) -> bool:
        """Delete a workspace owned by the user. Returns True if a row was removed."""
        user_email = normalize_user_email(user_email)
        with self._get_session() as session:
            record = self._find(session, workspace_id, user_email)
            if not record:
                return False
            session.delete(record)
            session.commit()
            logger.info("Deleted workspace %s for %s", workspace_id, user_email)
            return True
