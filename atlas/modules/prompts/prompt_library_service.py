"""Team-shared prompt library: authorization and registry operations (issue #1169).

Every read, write and execution of a library prompt goes through
``PromptLibraryService``. The repository below it stores rows and decides
nothing; this module decides who may do what, using the identity the server
authenticated and Atlas's existing group check (``is_user_in_group``).

Permission model (strictly ordered; a level includes every level below it):

* ``use``    -- see the entry and its published versions; select one for chat;
               fork it. Granted by membership in a shared group, or by a
               direct user grant.
* ``edit``   -- also change the draft and publish new versions. Direct user
               grant only: group membership never grants more than ``use``.
* ``manage`` -- also change sharing and read the audit trail. Direct user
               grant only; only the owner may grant or revoke ``manage``.
* ``owner``  -- the creator. Everything above, plus archiving.

Grants combine as a union: a user's effective level is the highest level any
one grant gives them, so losing one group never removes access that another
group or a direct grant still provides. An entry with no published version is
invisible to ``use``-only users (there is nothing for them to use).

Membership is checked on every request -- never cached here -- so adding or
removing someone from a group takes effect on their next request, including a
chat turn. A group check that errors counts as "not a member" (fail closed).

Callers who lack any access get the same answer as for an id that does not
exist (``PromptNotFound``), so ids cannot be probed. ``PromptForbidden`` is
only raised to callers who can already see the entry.
"""

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email
from atlas.modules.chat_history.prompt_library_repository import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PromptLibraryRepository,
    normalize_group_name,
)
from atlas.modules.prompts.prompt_library_policy import (  # noqa: F401 - re-exported
    _GROUP_CHECK_CONCURRENCY,
    _GROUP_NAME_RE,
    LEVEL_EDIT,
    LEVEL_MANAGE,
    LEVEL_NAMES,
    LEVEL_NONE,
    LEVEL_OWNER,
    LEVEL_USE,
    MAX_CHANGE_DESCRIPTION_LEN,
    MAX_CONTENT_LEN,
    MAX_DESCRIPTION_LEN,
    MAX_GRANTS,
    MAX_TITLE_LEN,
    ORG_WIDE_GROUPS,
    PERMISSION_LEVELS,
    SCOPES,
    Access,
    GroupCandidates,
    GroupCheck,
    PromptForbidden,
    PromptLibraryError,
    PromptNotFound,
    PromptUnavailable,
    PromptValidationError,
    _default_group_candidates,
    clean_content,
    clean_description,
    clean_title,
    parse_grants,
    sharing_view,
    summary_view,
    version_view,
)

logger = logging.getLogger(__name__)


class PromptLibraryService:
    """Authorization-checked operations on the prompt library."""

    def __init__(
        self,
        repository: PromptLibraryRepository,
        group_check: Optional[GroupCheck] = None,
        group_candidates: Optional[GroupCandidates] = None,
    ):
        self._repo = repository
        self._group_check = group_check
        self._group_candidates = group_candidates or _default_group_candidates

    @property
    def repository(self) -> PromptLibraryRepository:
        return self._repo

    # ------------------------------------------------------------------
    # Membership and access
    # ------------------------------------------------------------------

    async def _is_member(self, user_email: str, group: str) -> bool:
        check = self._group_check
        if check is None:
            # Resolved per call so the live auth module (and test patches of
            # it) is always the one consulted.
            from atlas.core.auth import is_user_in_group

            check = is_user_in_group
        try:
            return bool(await check(user_email, group))
        except Exception as e:
            # Fail closed: an authorizer outage must not grant access.
            logger.warning(
                "Group check failed for prompt-library access; treating as not a member: %s",
                type(e).__name__,
            )
            return False

    async def _member_groups(self, user_email: str, groups: Iterable[str]) -> Set[str]:
        names = sorted({g for g in groups if g})
        if not names:
            return set()
        semaphore = asyncio.Semaphore(_GROUP_CHECK_CONCURRENCY)

        async def check(group: str) -> Optional[str]:
            async with semaphore:
                return group if await self._is_member(user_email, group) else None

        results = await asyncio.gather(*(check(g) for g in names))
        return {g for g in results if g}

    async def _access(
        self,
        user_email: str,
        prompt: Dict[str, Any],
        grants: Optional[List[Dict[str, Any]]] = None,
        member_groups: Optional[Set[str]] = None,
    ) -> Access:
        if prompt is None or prompt.get("archived_at"):
            return Access()
        if prompt["owner_email"] == user_email:
            return Access(LEVEL_OWNER, ["owner"])
        if grants is None:
            grants = self._repo.list_grants(prompt["id"])
        level = LEVEL_NONE
        via: List[str] = []
        for g in grants:
            if g["grantee_type"] == GRANTEE_USER and g["grantee"] == user_email:
                level = max(level, PERMISSION_LEVELS.get(g["permission"], LEVEL_NONE))
                via.append("user")
        # An org-wide group never grants access, even if a row for one exists
        # (written outside this service): defense in depth for ORG_WIDE_GROUPS.
        group_names = [
            g["grantee"] for g in grants
            if g["grantee_type"] == GRANTEE_GROUP and g["grantee"] not in ORG_WIDE_GROUPS
        ]
        if group_names:
            if member_groups is None:
                matched = await self._member_groups(user_email, group_names)
            else:
                matched = {g for g in group_names if g in member_groups}
            if matched:
                level = max(level, LEVEL_USE)
                via.extend(sorted(matched))
        # A use-only caller has nothing to use until something is published.
        if level == LEVEL_USE and not prompt.get("latest_version_number"):
            return Access()
        return Access(level, via)

    async def _authorize(self, user_email: str, prompt_id: str, minimum: int):
        user_email = normalize_user_email(user_email)
        if not user_email:
            raise PromptNotFound()
        prompt = self._repo.get_prompt(prompt_id)
        if prompt is None:
            raise PromptNotFound()
        access = await self._access(user_email, prompt)
        if access.level == LEVEL_NONE:
            raise PromptNotFound()
        if access.level < minimum:
            raise PromptForbidden(
                f"This action requires {LEVEL_NAMES.get(minimum, 'more')} permission on the prompt"
            )
        return user_email, prompt, access

    def _audit(self, prompt_id: str, event: str, actor: str, version_id: Optional[str] = None,
               details: Optional[Dict[str, Any]] = None) -> None:
        try:
            self._repo.add_audit_event(prompt_id, event, actor, version_id=version_id, details=details)
        except Exception as e:  # an audit write failure is logged, never silent
            logger.error(
                "Failed to record prompt-library audit event %s for %s: %s",
                event, sanitize_for_logging(prompt_id), type(e).__name__,
            )

    # ------------------------------------------------------------------
    # Listing and reading
    # ------------------------------------------------------------------

    async def list_prompts(
        self, user_email: str, scope: str = "all", query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Entries this user may see, filtered by scope and an optional search.

        scope: ``mine`` (owned), ``shared`` (direct user grant), ``team``
        (through a group the user is in), ``all`` (union).
        """
        if scope not in SCOPES:
            raise PromptValidationError(f"scope must be one of {', '.join(SCOPES)}")
        user_email = normalize_user_email(user_email)
        if not user_email:
            return []

        owned = set(self._repo.list_owned_prompt_ids(user_email)) if scope in ("all", "mine") else set()
        direct = self._repo.prompt_ids_granted_to_user(user_email) if scope in ("all", "shared") else {}
        team: Dict[str, List[str]] = {}
        member_groups: Set[str] = set()
        if scope in ("all", "team"):
            member_groups = await self._member_groups(
                user_email,
                [g for g in self._repo.distinct_granted_groups() if g not in ORG_WIDE_GROUPS],
            )
            team = self._repo.prompt_ids_granted_to_groups(member_groups)

        candidate_ids = owned | set(direct) | set(team)
        if scope == "shared":
            candidate_ids -= set(self._repo.list_owned_prompt_ids(user_email))
        if scope == "team":
            candidate_ids -= set(self._repo.list_owned_prompt_ids(user_email))
        if query:
            candidate_ids = set(self._repo.search_prompt_ids(list(candidate_ids), query))
        prompts = self._repo.get_prompts(candidate_ids)
        grants_by_prompt = self._repo.grants_for_prompts(p["id"] for p in prompts)

        results = []
        for prompt in prompts:
            grants = grants_by_prompt.get(prompt["id"], [])
            # Recompute the level from the full grant set (the scope queries
            # only found *a* path in); the membership answers are reused.
            access = await self._access(user_email, prompt, grants, member_groups if scope in ("all", "team") else None)
            if access.level == LEVEL_NONE:
                continue
            latest = self._repo.get_latest_version(prompt["id"]) if prompt.get("latest_version_id") else None
            results.append(summary_view(prompt, access, grants, latest))
        return results

    async def get_prompt(self, user_email: str, prompt_id: str) -> Dict[str, Any]:
        user_email, prompt, access = await self._authorize(user_email, prompt_id, LEVEL_USE)
        grants = self._repo.list_grants(prompt_id)
        latest = self._repo.get_latest_version(prompt_id) if prompt.get("latest_version_id") else None
        data = summary_view(prompt, access, grants, latest)
        if access.level >= LEVEL_EDIT:
            data["draft_content"] = prompt["draft_content"]
        if latest is not None:
            data["latest_content"] = latest["content"]
        return data

    async def list_versions(self, user_email: str, prompt_id: str) -> List[Dict[str, Any]]:
        await self._authorize(user_email, prompt_id, LEVEL_USE)
        return [version_view(v) for v in self._repo.list_versions(prompt_id)]

    async def get_version(self, user_email: str, prompt_id: str, version_id: str) -> Dict[str, Any]:
        await self._authorize(user_email, prompt_id, LEVEL_USE)
        version = self._repo.get_version(prompt_id, version_id)
        if version is None:
            raise PromptNotFound()
        return version_view(version)

    async def export_prompt(self, user_email: str, prompt_id: str) -> Dict[str, Any]:
        """A portable JSON view: the entry plus every published version.

        The draft is included only for editors, sharing only for managers --
        the same rules as the detail view.
        """
        detail = await self.get_prompt(user_email, prompt_id)
        detail.pop("latest_content", None)
        detail["versions"] = [version_view(v) for v in self._repo.list_versions(prompt_id)]
        detail["export_format"] = "atlas-prompt-library/1"
        return detail

    async def list_audit_events(self, user_email: str, prompt_id: str) -> List[Dict[str, Any]]:
        await self._authorize(user_email, prompt_id, LEVEL_MANAGE)
        return self._repo.list_audit_events(prompt_id)

    async def shareable_groups(self, user_email: str) -> List[str]:
        """Configured groups the caller belongs to (the only ones they may share with)."""
        user_email = normalize_user_email(user_email)
        if not user_email:
            return []
        candidates = {normalize_group_name(g) for g in self._group_candidates()} - ORG_WIDE_GROUPS
        return sorted(await self._member_groups(user_email, candidates))

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    async def create_prompt(
        self, user_email: str, title: str, content: str, description: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a private entry (no grants) owned by the caller."""
        user_email = normalize_user_email(user_email)
        if not user_email:
            raise PromptValidationError("Missing user identity")
        title = clean_title(title, required=True)
        content = clean_content(content, required=True)
        description = clean_description(description)
        prompt = self._repo.create_prompt(user_email, title, description, content)
        self._audit(prompt["id"], "create", user_email)
        return await self.get_prompt(user_email, prompt["id"])

    async def update_prompt(
        self,
        user_email: str,
        prompt_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_email, prompt, _ = await self._authorize(user_email, prompt_id, LEVEL_EDIT)
        title = clean_title(title, required=False)
        content = clean_content(content, required=False)
        description = clean_description(description)
        changed = [
            name for name, value, current in (
                ("title", title, prompt["title"]),
                ("description", description, prompt.get("description") or ""),
                ("draft_content", content, prompt["draft_content"]),
            )
            if value is not None and value != current
        ]
        if changed:
            if self._repo.update_prompt(prompt_id, title, description, content) is None:
                raise PromptNotFound()
            self._audit(prompt_id, "update_draft", user_email, details={"fields": changed})
        return await self.get_prompt(user_email, prompt_id)

    async def publish(
        self, user_email: str, prompt_id: str, change_description: Optional[str] = None
    ) -> Dict[str, Any]:
        user_email, _, _ = await self._authorize(user_email, prompt_id, LEVEL_EDIT)
        if change_description is not None and len(change_description) > MAX_CHANGE_DESCRIPTION_LEN:
            raise PromptValidationError(
                f"Change description is longer than {MAX_CHANGE_DESCRIPTION_LEN} characters"
            )
        version = self._repo.publish_version(prompt_id, user_email, change_description)
        if version is None:
            raise PromptNotFound()
        self._audit(
            prompt_id, "publish", user_email, version_id=version["id"],
            details={"version_number": version["version_number"]},
        )
        return version_view(version)

    async def get_sharing(self, user_email: str, prompt_id: str) -> Dict[str, Any]:
        await self._authorize(user_email, prompt_id, LEVEL_MANAGE)
        return sharing_view(self._repo.list_grants(prompt_id))

    async def set_sharing(
        self,
        user_email: str,
        prompt_id: str,
        groups: Sequence[str],
        users: Sequence[Dict[str, str]],
    ) -> Dict[str, Any]:
        """Replace the entry's grants.

        Rules: groups get ``use`` only; a newly added group must be one the
        caller belongs to (no sharing into groups you are not part of); user
        grants may be ``use``/``edit``/``manage`` but only the owner may add,
        change or remove ``manage``; the owner is never a grantee.
        """
        user_email, prompt, access = await self._authorize(user_email, prompt_id, LEVEL_MANAGE)
        current = self._repo.list_grants(prompt_id)
        current_groups = {g["grantee"] for g in current if g["grantee_type"] == GRANTEE_GROUP}
        current_users = {g["grantee"]: g["permission"] for g in current if g["grantee_type"] == GRANTEE_USER}

        new_groups, new_users = parse_grants(groups, users, prompt["owner_email"])

        if access.level < LEVEL_OWNER:
            before = {e for e, p in current_users.items() if p == "manage"}
            after = {e for e, p in new_users.items() if p == "manage"}
            if before != after:
                raise PromptForbidden("Only the owner can grant or revoke manage permission")

        added_groups = new_groups - current_groups
        if added_groups:
            member = await self._member_groups(user_email, added_groups)
            not_member = added_groups - member
            if not_member:
                raise PromptForbidden(
                    "You can only share with groups you belong to: " + ", ".join(sorted(not_member))
                )

        grants = [
            {"grantee_type": GRANTEE_GROUP, "grantee": g, "permission": "use"} for g in sorted(new_groups)
        ] + [
            {"grantee_type": GRANTEE_USER, "grantee": e, "permission": p} for e, p in sorted(new_users.items())
        ]
        stored = self._repo.replace_grants(prompt_id, grants, user_email)

        details = {
            "groups_added": sorted(added_groups),
            "groups_removed": sorted(current_groups - new_groups),
            "users_added": sorted(set(new_users) - set(current_users)),
            "users_removed": sorted(set(current_users) - set(new_users)),
            "users_changed": sorted(
                e for e in set(new_users) & set(current_users) if new_users[e] != current_users[e]
            ),
        }
        if any(details.values()):
            self._audit(prompt_id, "share", user_email, details=details)
        return sharing_view(stored)

    async def fork(
        self,
        user_email: str,
        prompt_id: str,
        version_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Copy a published version into a new private entry owned by the caller.

        Grants are never copied: the fork starts private, and later changes to
        either entry do not affect the other.
        """
        user_email, prompt, _ = await self._authorize(user_email, prompt_id, LEVEL_USE)
        if version_id:
            source = self._repo.get_version(prompt_id, version_id)
        else:
            source = self._repo.get_latest_version(prompt_id)
        if source is None:
            raise PromptNotFound()
        new_title = clean_title(title, required=False) or f"{source['title']} (copy)"[:MAX_TITLE_LEN]
        new_prompt = self._repo.create_prompt(
            user_email,
            new_title,
            prompt.get("description"),
            source["content"],
            forked_from_prompt_id=prompt_id,
            forked_from_version_id=source["id"],
        )
        self._audit(
            prompt_id, "fork", user_email, version_id=source["id"],
            details={"new_prompt_id": new_prompt["id"], "version_number": source["version_number"]},
        )
        self._audit(
            new_prompt["id"], "create", user_email,
            details={"forked_from_prompt_id": prompt_id, "forked_from_version_id": source["id"]},
        )
        return await self.get_prompt(user_email, new_prompt["id"])

    async def archive(self, user_email: str, prompt_id: str) -> None:
        """Hide the entry and stop new use. Versions are kept for history."""
        user_email, _, _ = await self._authorize(user_email, prompt_id, LEVEL_OWNER)
        if not self._repo.archive_prompt(prompt_id):
            raise PromptNotFound()
        self._audit(prompt_id, "archive", user_email)

    # ------------------------------------------------------------------
    # Chat execution
    # ------------------------------------------------------------------

    async def resolve_for_execution(
        self, user_email: str, prompt_id: str, version_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """The exact published version a chat turn may run, after a fresh check.

        ``version_id`` None means the latest published version. Raises
        ``PromptUnavailable`` -- one message for every reason -- when the entry
        is missing, archived, not visible to this user (now), or the version
        does not belong to it. Never falls back to another version.

        Runs on the chat path before the model call, so the database reads go
        through ``asyncio.to_thread`` (as conversation hydration does) rather
        than blocking every other socket on this replica.
        """
        user_email = normalize_user_email(user_email)
        if not user_email or not prompt_id:
            raise PromptUnavailable()
        prompt = await asyncio.to_thread(self._repo.get_prompt, prompt_id)
        if prompt is None:
            raise PromptUnavailable()
        grants = await asyncio.to_thread(self._repo.list_grants, prompt_id)
        access = await self._access(user_email, prompt, grants)
        if access.level == LEVEL_NONE:
            # Recorded for an entry that exists (archived or revoked); ids that
            # match nothing leave no row behind.
            await asyncio.to_thread(self._audit, prompt_id, "use_denied", user_email, version_id)
            raise PromptUnavailable()
        if version_id:
            version = await asyncio.to_thread(self._repo.get_version, prompt_id, version_id)
        else:
            version = await asyncio.to_thread(self._repo.get_latest_version, prompt_id)
        if version is None:
            raise PromptUnavailable()
        return version

    async def record_use(
        self, user_email: str, version: Dict[str, Any], conversation_id: Optional[str]
    ) -> None:
        """Audit the moment a conversation is pinned to a version."""
        await asyncio.to_thread(
            self._audit, version["prompt_id"], "use", user_email, version["id"],
            {"version_number": version["version_number"], "conversation_id": conversation_id},
        )
