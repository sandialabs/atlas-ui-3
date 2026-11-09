"""Prompt-library policy pieces (issue #1169): permission levels, errors,
input validation, and what each level may see in a response.

Kept apart from ``prompt_library_service`` so the access rules that shape
responses are in one place and the service module stays readable. The model
itself is documented in ``prompt_library_service``.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from atlas.core.user_identity import normalize_user_email
from atlas.modules.chat_history.prompt_library_repository import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    normalize_group_name,
)

logger = logging.getLogger(__name__)

GroupCheck = Callable[[str, str], Awaitable[bool]]
GroupCandidates = Callable[[], Iterable[str]]

LEVEL_NONE = 0
LEVEL_USE = 1
LEVEL_EDIT = 2
LEVEL_MANAGE = 3
LEVEL_OWNER = 4

PERMISSION_LEVELS = {"use": LEVEL_USE, "edit": LEVEL_EDIT, "manage": LEVEL_MANAGE}
LEVEL_NAMES = {
    LEVEL_USE: "use",
    LEVEL_EDIT: "edit",
    LEVEL_MANAGE: "manage",
    LEVEL_OWNER: "owner",
}

SCOPES = ("all", "mine", "shared", "team")

MAX_TITLE_LEN = 200
MAX_DESCRIPTION_LEN = 1000
MAX_CONTENT_LEN = 50_000
MAX_CHANGE_DESCRIPTION_LEN = 1000
MAX_GRANTS = 100
# Concurrent group checks per request: an external authorizer is an HTTP call
# per (user, group), so a large sharing footprint must not fan out unbounded.
_GROUP_CHECK_CONCURRENCY = 8
# Groups that stand for "every user", which the MVP never accepts as a
# sharing target: sharing with one would be an organization-wide catalog,
# which issue #1169 leaves out of scope. ``users`` is the group
# ``is_user_in_group`` grants to every identity when no external authorizer
# is configured. Enforced here and in access evaluation, not only in the UI.
ORG_WIDE_GROUPS = frozenset({"users"})

_GROUP_NAME_RE = re.compile(r"^[^\s\x00-\x1f]{1,255}$")


class PromptLibraryError(Exception):
    """Base error; ``message`` is safe to show the caller."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class PromptNotFound(PromptLibraryError):
    """Missing, archived, or not visible to this caller (indistinguishable)."""

    def __init__(self) -> None:
        super().__init__("Prompt not found")


class PromptForbidden(PromptLibraryError):
    """The caller can see the entry but lacks the level this action needs."""


class PromptValidationError(PromptLibraryError):
    """The request is malformed."""


class PromptUnavailable(PromptLibraryError):
    """A chat turn named a prompt/version this user may not run right now."""

    def __init__(self) -> None:
        super().__init__(
            "The selected team prompt is not available. It may have been "
            "unshared or archived; choose another prompt to continue."
        )


@dataclass
class Access:
    level: int = LEVEL_NONE
    # How the caller reached the entry: "owner", "user", or group names.
    via: List[str] = field(default_factory=list)

    @property
    def name(self) -> Optional[str]:
        return LEVEL_NAMES.get(self.level)


def _default_group_candidates() -> List[str]:
    """Group names Atlas already knows from its configuration.

    Offered as sharing targets (filtered to the caller's own memberships).
    Read from the same config the MCP/RAG/model access checks use, so the
    library introduces no group list of its own.
    """
    names: Set[str] = set()
    try:
        from atlas.modules.config.config_manager import config_manager

        settings = config_manager.app_settings
        if settings.admin_group:
            names.add(settings.admin_group)
        names.update(settings.static_group_members.keys())
        for server in (config_manager.mcp_config.servers or {}).values():
            names.update(server.groups or [])
        for source in (config_manager.rag_sources_config.sources or {}).values():
            names.update(getattr(source, "groups", None) or [])
        for model in (config_manager.llm_config.models or {}).values():
            names.update(getattr(model, "groups", None) or [])
    except Exception as e:  # pragma: no cover - config shape drift must not break sharing
        logger.warning("Could not collect configured group names: %s", type(e).__name__)
    return sorted(
        {normalize_group_name(n) for n in names if normalize_group_name(n)} - ORG_WIDE_GROUPS
    )




def clean_title(title: Optional[str], required: bool) -> Optional[str]:
    if title is None:
        if required:
            raise PromptValidationError("Title is required")
        return None
    title = title.strip()
    if not title:
        raise PromptValidationError("Title cannot be empty")
    if len(title) > MAX_TITLE_LEN:
        raise PromptValidationError(f"Title is longer than {MAX_TITLE_LEN} characters")
    return title

def clean_content(content: Optional[str], required: bool) -> Optional[str]:
    if content is None:
        if required:
            raise PromptValidationError("Content is required")
        return None
    if not content.strip():
        raise PromptValidationError("Content cannot be empty")
    if len(content) > MAX_CONTENT_LEN:
        raise PromptValidationError(f"Content is longer than {MAX_CONTENT_LEN} characters")
    return content

def clean_description(description: Optional[str]) -> Optional[str]:
    if description is None:
        return None
    if len(description) > MAX_DESCRIPTION_LEN:
        raise PromptValidationError(f"Description is longer than {MAX_DESCRIPTION_LEN} characters")
    return description


def summary_view(
    prompt: Dict[str, Any],
    access: Access,
    grants: Optional[List[Dict[str, Any]]] = None,
    latest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    latest_ref = None
    if prompt.get("latest_version_id"):
        latest_ref = {
            "id": prompt["latest_version_id"],
            "version_number": prompt["latest_version_number"],
        }
        if latest is not None:
            latest_ref["created_at"] = latest.get("created_at")
            latest_ref["author_email"] = latest.get("author_email")
    data: Dict[str, Any] = {
        "id": prompt["id"],
        "title": prompt["title"],
        "description": prompt.get("description"),
        "owner_email": prompt["owner_email"],
        "is_owner": access.level == LEVEL_OWNER,
        "permission": access.name,
        "access_via": access.via,
        "latest_version": latest_ref,
        "created_at": prompt.get("created_at"),
        "updated_at": prompt.get("updated_at"),
        "can_edit": access.level >= LEVEL_EDIT,
        "can_manage": access.level >= LEVEL_MANAGE,
    }
    if access.level >= LEVEL_EDIT:
        data["forked_from_prompt_id"] = prompt.get("forked_from_prompt_id")
        data["forked_from_version_id"] = prompt.get("forked_from_version_id")
        if latest is not None:
            data["has_unpublished_changes"] = (
                latest["content"] != prompt["draft_content"] or latest["title"] != prompt["title"]
            )
        else:
            data["has_unpublished_changes"] = True
    if grants is not None:
        if access.level >= LEVEL_MANAGE:
            data["sharing"] = sharing_view(grants)
        # Everyone may know whether an entry is shared at all.
        data["shared"] = bool(grants)
    return data

def sharing_view(grants: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "groups": sorted(g["grantee"] for g in grants if g["grantee_type"] == GRANTEE_GROUP),
        "users": sorted(
            (
                {"email": g["grantee"], "permission": g["permission"]}
                for g in grants
                if g["grantee_type"] == GRANTEE_USER
            ),
            key=lambda u: u["email"],
        ),
    }

def version_view(version: Dict[str, Any], include_content: bool = True) -> Dict[str, Any]:
    data = {
        "id": version["id"],
        "prompt_id": version["prompt_id"],
        "version_number": version["version_number"],
        "title": version["title"],
        "change_description": version.get("change_description"),
        "author_email": version["author_email"],
        "created_at": version.get("created_at"),
    }
    if include_content:
        data["content"] = version["content"]
    return data


def parse_grants(
    groups: Sequence[str], users: Sequence[Dict[str, str]], owner_email: str
) -> Tuple[Set[str], Dict[str, str]]:
    """Validate and normalize a requested grant set.

    Groups become lower-cased names (always ``use``); users become
    ``{normalized_email: permission}``. The owner can never be a grantee.
    """
    new_groups: Set[str] = set()
    for raw in groups or []:
        name = normalize_group_name(raw if isinstance(raw, str) else "")
        if not name or not _GROUP_NAME_RE.match(name):
            raise PromptValidationError("Invalid group name")
        if name in ORG_WIDE_GROUPS:
            raise PromptValidationError(
                f"Sharing with '{name}' (everyone) is not supported; choose a narrower group"
            )
        new_groups.add(name)

    new_users: Dict[str, str] = {}
    for entry in users or []:
        if not isinstance(entry, dict):
            raise PromptValidationError("Invalid user grant")
        email = normalize_user_email(entry.get("email") or "")
        permission = entry.get("permission") or "use"
        if not email or len(email) > 255 or any(c.isspace() for c in email):
            raise PromptValidationError("Invalid user identity in sharing")
        if permission not in PERMISSION_LEVELS:
            raise PromptValidationError("Permission must be use, edit or manage")
        if email == owner_email:
            raise PromptValidationError("The owner already has full access")
        new_users[email] = permission

    if len(new_groups) + len(new_users) > MAX_GRANTS:
        raise PromptValidationError(f"At most {MAX_GRANTS} grants per prompt")

    return new_groups, new_users
