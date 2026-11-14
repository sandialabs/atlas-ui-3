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
    clean_group_name,
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
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
# Groups that stand for "every user", which the MVP never accepts as a
# sharing target: sharing with one would be an organization-wide catalog,
# which issue #1169 leaves out of scope. ``users`` is the group
# ``is_user_in_group`` grants to every identity when no external authorizer
# is configured. Enforced here and in access evaluation, not only in the UI.
# Compared case- and whitespace-insensitively (``is_org_wide_group``), while
# every other group name keeps its configured spelling.
ORG_WIDE_GROUPS = frozenset({"users"})


def check_revision(value: Any, name: str) -> int:
    """A client-supplied optimistic-concurrency revision (a non-negative int)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PromptValidationError(f"{name} is required")
    return value


def is_org_wide_group(name: str) -> bool:
    return (name or "").strip().casefold() in ORG_WIDE_GROUPS

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


class PromptConflict(PromptLibraryError):
    """Someone else changed the entry first; reload and retry (HTTP 409)."""

    _MESSAGES = {
        "draft": "The draft was changed by someone else. Reload it, review the changes, and try again.",
        "sharing": "Sharing was changed by someone else. Reload the sharing settings and try again.",
        "publish": "Another version is being published right now. Try again in a moment.",
    }

    def __init__(self, kind: str) -> None:
        super().__init__(self._MESSAGES.get(kind, "The prompt was changed concurrently. Try again."))
        self.kind = kind


class PromptLibraryUnavailable(PromptLibraryError):
    """The library's storage failed; nothing about the cause is exposed (HTTP 503)."""

    def __init__(self) -> None:
        super().__init__("The prompt library is temporarily unavailable. Try again shortly.")


class GroupCheckUnavailable(PromptLibraryUnavailable):
    """Group membership could not be confirmed (authorizer slow or failing)."""

    def __init__(self) -> None:
        PromptLibraryError.__init__(
            self,
            "Group membership could not be checked right now, so sharing was not "
            "changed. Try again shortly.",
        )


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
    return sorted({clean_group_name(n) for n in names if clean_group_name(n) and not is_org_wide_group(n)})




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
        # The draft revision an edit or publish must name (optimistic concurrency).
        data["draft_revision"] = prompt.get("draft_revision", 1)
        data["forked_from_prompt_id"] = prompt.get("forked_from_prompt_id")
        data["forked_from_version_id"] = prompt.get("forked_from_version_id")
        if "has_unpublished_changes" in prompt:
            # A listing row: compared in the database, no text loaded.
            data["has_unpublished_changes"] = bool(prompt["has_unpublished_changes"])
        elif latest is not None:
            data["has_unpublished_changes"] = (
                latest["content"] != prompt["draft_content"] or latest["title"] != prompt["title"]
            )
        else:
            data["has_unpublished_changes"] = True
    if grants is not None:
        if access.level >= LEVEL_MANAGE:
            data["sharing"] = sharing_view(grants, prompt.get("sharing_revision", 0))
        # Everyone may know whether an entry is shared at all.
        data["shared"] = bool(grants)
    return data


def sharing_view(grants: List[Dict[str, Any]], revision: int) -> Dict[str, Any]:
    """Grants as the API shows them, with the revision a change must name."""
    return {
        "revision": revision,
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

    Groups keep their configured spelling, surrounding whitespace removed
    (always ``use``); users become ``{normalized_email: permission}``. The
    owner can never be a grantee, and the everyone group never is either.
    """
    new_groups: Set[str] = set()
    for raw in groups or []:
        name = clean_group_name(raw if isinstance(raw, str) else "")
        if not name or not _GROUP_NAME_RE.match(name):
            raise PromptValidationError("Invalid group name")
        if is_org_wide_group(name):
            raise PromptValidationError(
                "Sharing with the everyone group 'users' is not supported; choose a narrower group"
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
