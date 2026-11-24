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

Group names are passed to the authorizer exactly as configured (it may match
case-sensitively); only the reserved everyone group ``users`` is compared
case-insensitively, and it never grants anything.

Every repository call runs in a worker thread (``_db``): the repository is
synchronous, publishing can back off between conflicting attempts, and a
database round trip must never stall the event loop that serves every
WebSocket on this replica. Each call opens and closes its own session inside
that thread, so no session is shared across threads. Storage failures become
``PromptLibraryUnavailable`` and concurrent-change refusals ``PromptConflict``.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, TypeVar

from atlas.core.user_identity import normalize_user_email
from atlas.modules.chat_history.prompt_library_repository import (
    GRANTEE_GROUP,
    GRANTEE_USER,
    PromptConflictError,
    PromptLibraryRepository,
    PromptLimitError,
    PromptStorageError,
)
from atlas.modules.prompts.prompt_library_listing import PromptLibraryListing
from atlas.modules.prompts.prompt_library_membership import MembershipResolver
from atlas.modules.prompts.prompt_library_policy import (
    LEVEL_EDIT,
    LEVEL_MANAGE,
    LEVEL_NAMES,
    LEVEL_NONE,
    LEVEL_OWNER,
    LEVEL_USE,
    MAX_CHANGE_DESCRIPTION_LEN,
    MAX_PAGE_SIZE,
    PERMISSION_LEVELS,
    Access,
    GroupCandidates,
    GroupCheck,
    PromptConflict,
    PromptForbidden,
    PromptLibraryUnavailable,
    PromptLimitReached,
    PromptMembershipUnknown,
    PromptNotFound,
    PromptUnavailable,
    PromptValidationError,
    _default_group_candidates,
    check_revision,
    clean_content,
    clean_description,
    clean_title,
    is_org_wide_group,
    summary_view,
    version_view,
)
from atlas.modules.prompts.prompt_library_sharing import PromptLibrarySharing

logger = logging.getLogger(__name__)


def _check_page(limit: Any, offset: Any) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise PromptValidationError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise PromptValidationError("offset must be zero or more")

# Concurrent group checks per request: an external authorizer is an HTTP call
# per (user, group), so a large sharing footprint must not fan out unbounded.
GROUP_CHECK_CONCURRENCY = 8
# Listing a library checks the caller against the groups named in grants.
# Bounded twice: at most this many groups (the most-granted first), and the
# whole membership pass within this many seconds. Past either bound the
# listing is marked incomplete and leaves the unchecked groups' entries out
# (fail closed); an entry is still usable by reference, where its own grants
# are checked.
MAX_LISTING_GROUP_CHECKS = 100
LISTING_GROUP_CHECK_TIMEOUT_S = 10.0
# The groups offered as sharing targets (GET /api/prompts/groups) are bounded
# the same way. Only groups whose check *completed* with "member" are offered;
# an unchecked group is never presented as one the caller may share with.
MAX_SHAREABLE_GROUP_CHECKS = 100
SHAREABLE_GROUP_CHECK_TIMEOUT_S = 10.0
# One entry's group grants, checked when it is opened or run (a chat turn
# waits on this). At most this many groups (sorted by name, so the bound is
# deterministic; MAX_GRANTS already keeps real entries within it) and within
# this many seconds. A chat turn stops at the first confirmed group. Past
# either bound, an unchecked group is "unknown": never access (fail closed),
# and a turn gets the transient "could not be confirmed" refusal, not "no
# longer available".
MAX_PROMPT_GROUP_CHECKS = 100
PROMPT_GROUP_CHECK_TIMEOUT_S = 5.0
# Version history pages, and how many versions (with content) one export holds.
DEFAULT_VERSION_PAGE_SIZE = 50
# Audit trail: refused uses are one counted row per actor, prompt and window;
# the trail is paged and can be filtered so management events stay reachable.
USE_DENIED_DEDUP_WINDOW_S = 3600
AUDIT_PAGE_SIZE = 200
MAX_AUDIT_PAGE_SIZE = 500
AUDIT_CATEGORIES = ("all", "management", "use", "denied")
MAX_EXPORT_VERSIONS = 100

T = TypeVar("T")




class PromptLibraryService(PromptLibraryListing, PromptLibrarySharing):
    """Authorization-checked operations on the prompt library."""

    def __init__(
        self,
        repository: PromptLibraryRepository,
        group_check: Optional[GroupCheck] = None,
        group_candidates: Optional[GroupCandidates] = None,
        membership_cache: Any = None,
        membership_ttl: Optional[Callable[[], int]] = None,
        limits: Optional[Callable[[], Dict[str, int]]] = None,
    ):
        """``membership_cache``/``membership_ttl`` enable reuse of membership
        answers (see ``prompt_library_membership``); the app factory passes
        them. Without them every check asks the authorizer. ``limits`` returns
        ``{"prompts_per_owner": n, "versions_per_prompt": n}`` (0: unlimited)."""
        self._repo = repository
        self._group_check = group_check
        self._group_candidates = group_candidates or _default_group_candidates
        self._limits = limits or (lambda: {})
        self._resolver = MembershipResolver(
            check=self._resolve_check,
            ttl_seconds=membership_ttl or (lambda: 0),
            store=lambda: membership_cache,
        )

    def _limit(self, name: str) -> int:
        try:
            return max(0, int(self._limits().get(name) or 0))
        except (TypeError, ValueError):
            return 0

    def _resolve_check(self) -> GroupCheck:
        if self._group_check is not None:
            return self._group_check
        # Resolved per call so the live auth module (and test patches of it)
        # is always the one consulted. The tri-state check tells an
        # unavailable authorizer (None) apart from "not a member" (False).
        from atlas.core.auth import check_user_in_group

        return check_user_in_group

    @property
    def repository(self) -> PromptLibraryRepository:
        return self._repo

    async def _db(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a repository call off the event loop, mapping storage errors."""
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except PromptConflictError as e:
            raise PromptConflict(e.kind) from None
        except PromptLimitError as e:
            raise PromptLimitReached(e.kind, e.limit) from None
        except PromptStorageError:
            # Already logged (type and SQLSTATE only) by the storage guard.
            raise PromptLibraryUnavailable() from None

    # ------------------------------------------------------------------
    # Membership and access
    # ------------------------------------------------------------------

    async def _is_member(self, user_email: str, group: str) -> bool:
        return bool(await self._membership(user_email, group))

    async def _membership(self, user_email: str, group: str) -> Optional[bool]:
        """True/False from the authorizer, or None when the check itself failed
        (fail closed: never a member). Bounded and reused; see
        ``prompt_library_membership``."""
        return await self._resolver.membership(user_email, group)

    async def _member_groups_within(
        self, user_email: str, groups: List[str], timeout_s: float
    ) -> Tuple[Set[str], bool]:
        """Membership in ``groups`` within ``timeout_s``: ``(members, finished)``.

        Checks still running at the deadline are cancelled, and checks whose
        authorizer call failed are counted the same way: "not a member" (fail
        closed), and ``finished`` is False because the answer is unknown, not
        "no". Groups confirmed before the deadline are kept.
        """
        names = sorted({g for g in groups if g and not is_org_wide_group(g)})
        if not names:
            return set(), True
        semaphore = asyncio.Semaphore(GROUP_CHECK_CONCURRENCY)

        async def check(group: str) -> Tuple[str, Optional[bool]]:
            async with semaphore:
                return group, await self._membership(user_email, group)

        tasks = [asyncio.ensure_future(check(g)) for g in names]
        done, pending = await asyncio.wait(tasks, timeout=timeout_s)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        members: Set[str] = set()
        failed = bool(pending)
        for task in done:
            if task.cancelled() or task.exception() is not None:
                failed = True
                continue
            group, answer = task.result()
            if answer is None:
                failed = True
            elif answer:
                members.add(group)
        return members, not failed

    async def _prompt_member_groups(
        self,
        user_email: str,
        groups: List[str],
        unknown: Optional[Set[str]],
        first_only: bool = False,
    ) -> Set[str]:
        """The user's groups among one entry's grants, within the per-entry bounds.

        ``MAX_PROMPT_GROUP_CHECKS`` groups at most, within
        ``PROMPT_GROUP_CHECK_TIMEOUT_S``; with ``first_only`` (a chat turn) the
        checks stop at the first confirmed group. A group that was not checked,
        timed out or failed is not a member (fail closed) and goes into
        ``unknown`` -- unless membership was already confirmed, in which case
        nothing is unknown about the outcome.
        """
        names = sorted({g for g in groups if g and not is_org_wide_group(g)})
        if not names:
            return set()
        checked, skipped = names[:MAX_PROMPT_GROUP_CHECKS], names[MAX_PROMPT_GROUP_CHECKS:]
        if skipped:
            logger.warning("Prompt-library access checked only %d of an entry's groups", MAX_PROMPT_GROUP_CHECKS)
        semaphore = asyncio.Semaphore(GROUP_CHECK_CONCURRENCY)

        async def check(group: str) -> Tuple[str, Optional[bool]]:
            async with semaphore:
                return group, await self._membership(user_email, group)

        tasks = [asyncio.ensure_future(check(g)) for g in checked]
        members: Set[str] = set()
        open_names: Set[str] = set(checked)
        deadline = asyncio.get_running_loop().time() + PROMPT_GROUP_CHECK_TIMEOUT_S
        pending = set(tasks)
        try:
            while pending and not (first_only and members):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    if task.cancelled() or task.exception() is not None:
                        continue
                    group, answer = task.result()
                    if answer is not None:
                        open_names.discard(group)
                    if answer:
                        members.add(group)
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        if open_names or skipped:
            if not members:
                logger.warning("Prompt-library group checks for an entry did not finish; access is unknown")
            if unknown is not None and not members:
                unknown.update(open_names, skipped)
        return members

    async def _listing_member_groups(self, user_email: str) -> Tuple[Set[str], bool]:
        """The caller's groups among those granted, and whether the check was complete."""
        groups = await self._db(self._repo.granted_groups_by_use, MAX_LISTING_GROUP_CHECKS + 1)
        complete = len(groups) <= MAX_LISTING_GROUP_CHECKS
        if not complete:
            groups = groups[:MAX_LISTING_GROUP_CHECKS]
            logger.warning(
                "Prompt-library listing checked only the %d most-granted groups", MAX_LISTING_GROUP_CHECKS
            )
        member, finished = await self._member_groups_within(
            user_email, groups, LISTING_GROUP_CHECK_TIMEOUT_S
        )
        if not finished:
            # Fail closed: entries only an unchecked group would show are left
            # out rather than guessed.
            logger.warning("Prompt-library listing group checks timed out; some team entries omitted")
        return member, complete and finished

    async def _access(
        self,
        user_email: str,
        prompt: Dict[str, Any],
        grants: Optional[List[Dict[str, Any]]] = None,
        member_groups: Optional[Set[str]] = None,
        history: bool = False,
        unknown_groups: Optional[Set[str]] = None,
        first_only: bool = False,
    ) -> Access:
        """The caller's level on ``prompt``.

        An archived entry grants nothing (it cannot be listed, opened, run,
        edited, shared or forked), except for ``history=True``: the read-only
        history operations (version list, one version's text, export). Those
        keep the normal rules -- owner, current direct grants, current group
        membership -- so revocation still applies; archiving only stops new
        use (design note: a pinned version always resolves to its text).
        """
        if prompt is None or (prompt.get("archived_at") and not history):
            return Access()
        if prompt["owner_email"] == user_email:
            return Access(LEVEL_OWNER, ["owner"])
        if grants is None:
            grants = await self._db(self._repo.list_grants, prompt["id"])
        level = LEVEL_NONE
        via: List[str] = []
        for g in grants:
            if g["grantee_type"] == GRANTEE_USER and g["grantee"] == user_email:
                level = max(level, PERMISSION_LEVELS.get(g["permission"], LEVEL_NONE))
                via.append("user")
        # The everyone group never grants access, even if a row for one exists
        # (written outside this service): defense in depth for ORG_WIDE_GROUPS.
        group_names = [
            g["grantee"] for g in grants
            if g["grantee_type"] == GRANTEE_GROUP and not is_org_wide_group(g["grantee"])
        ]
        if group_names:
            if member_groups is not None:
                matched = {g for g in group_names if g in member_groups}
            elif first_only and level >= LEVEL_USE:
                # Running only needs "use", which a direct grant already gives.
                matched = set()
            else:
                matched = await self._prompt_member_groups(
                    user_email, group_names, unknown_groups, first_only=first_only
                )
            if matched:
                level = max(level, LEVEL_USE)
                via.extend(sorted(matched))
        # A use-only caller has nothing to use until something is published.
        if level == LEVEL_USE and not prompt.get("latest_version_number"):
            return Access()
        return Access(level, via)

    async def _authorize(self, user_email: str, prompt_id: str, minimum: int, history: bool = False):
        user_email = normalize_user_email(user_email)
        if not user_email:
            raise PromptNotFound()
        prompt = await self._db(self._repo.get_prompt, prompt_id)
        if prompt is None:
            raise PromptNotFound()
        access = await self._access(user_email, prompt, history=history)
        if access.level == LEVEL_NONE:
            raise PromptNotFound()
        if access.level < minimum:
            raise PromptForbidden(
                f"This action requires {LEVEL_NAMES.get(minimum, 'more')} permission on the prompt"
            )
        return user_email, prompt, access

    async def _audit(self, prompt_id: str, event: str, actor: str, version_id: Optional[str] = None,
                     details: Optional[Dict[str, Any]] = None, dedup_window_s: Optional[int] = None) -> None:
        try:
            await asyncio.to_thread(
                self._repo.add_audit_event, prompt_id, event, actor, version_id, details, dedup_window_s
            )
        except Exception as e:  # an audit write failure is logged, never silent
            # Event type and exception type only: the prompt id is
            # client-supplied, and the failure is the same for every prompt.
            logger.error("Failed to record a prompt-library audit event (%s): %s", event, type(e).__name__)

    # ------------------------------------------------------------------
    # Listing and reading
    # ------------------------------------------------------------------

    async def get_prompt(self, user_email: str, prompt_id: str) -> Dict[str, Any]:
        user_email, prompt, access = await self._authorize(user_email, prompt_id, LEVEL_USE)
        return await self._detail_view(prompt_id, prompt, access)

    async def _detail_view(self, prompt_id: str, prompt: Dict[str, Any], access: Any) -> Dict[str, Any]:
        """The detail view of ``prompt`` -- a row snapshot the caller already holds."""
        grants = await self._db(self._repo.list_grants, prompt_id)
        latest = await self._db(self._repo.get_latest_version, prompt_id) if prompt.get("latest_version_id") else None
        data = summary_view(prompt, access, grants, latest)
        if access.level >= LEVEL_EDIT:
            data["draft_content"] = prompt["draft_content"]
        if latest is not None:
            data["latest_content"] = latest["content"]
        return data

    async def list_versions(
        self,
        user_email: str,
        prompt_id: str,
        limit: int = DEFAULT_VERSION_PAGE_SIZE,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """One page of version history, newest first: ``{versions, total, limit, offset}``.

        Entries are summaries (number, title, author, change description,
        date) without the prompt text; ``get_version`` returns one version's
        full content.
        """
        _check_page(limit, offset)
        # History stays readable after archiving (see _access).
        _, _, access = await self._authorize(user_email, prompt_id, LEVEL_USE, history=True)
        total, versions = await self._db(self._repo.page_versions, prompt_id, limit, offset, False)
        return {
            "versions": [version_view(v, include_content=False, include_author_email=access.level >= LEVEL_EDIT) for v in versions],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    async def get_version(self, user_email: str, prompt_id: str, version_id: str) -> Dict[str, Any]:
        # History stays readable after archiving (see _access).
        _, _, access = await self._authorize(user_email, prompt_id, LEVEL_USE, history=True)
        version = await self._db(self._repo.get_version, prompt_id, version_id)
        if version is None:
            raise PromptNotFound()
        return version_view(version, include_author_email=access.level >= LEVEL_EDIT)

    async def export_prompt(self, user_email: str, prompt_id: str, offset: int = 0) -> Dict[str, Any]:
        """A portable JSON view: the entry plus its published versions with content.

        Bounded: at most ``MAX_EXPORT_VERSIONS`` versions per response, newest
        first from ``offset``. ``versions_total`` and ``versions_truncated``
        say whether more remain (request the next ``offset`` to continue).
        The draft is included only for editors, sharing only for managers --
        the same rules as the detail view. An archived entry stays exportable
        by the callers the normal rules admit (``archived`` is true): export
        is a read-only copy of its history.
        """
        _check_page(MAX_EXPORT_VERSIONS, offset)
        user_email, prompt, access = await self._authorize(user_email, prompt_id, LEVEL_USE, history=True)
        detail = await self._detail_view(prompt_id, prompt, access)
        detail.pop("latest_content", None)
        detail["archived"] = bool(prompt.get("archived_at"))
        total, versions = await self._db(self._repo.page_versions, prompt_id, MAX_EXPORT_VERSIONS, offset, True)
        detail["versions"] = [version_view(v, include_author_email=access.level >= LEVEL_EDIT) for v in versions]
        detail["versions_total"] = total
        detail["versions_offset"] = offset
        detail["versions_truncated"] = offset + len(versions) < total
        detail["export_format"] = "atlas-prompt-library/1"
        return detail

    async def list_audit_events(
        self,
        user_email: str,
        prompt_id: str,
        category: str = "all",
        limit: int = AUDIT_PAGE_SIZE,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """One page of the audit trail, newest first: ``{events, total, limit, offset}``.

        ``category``: ``all``; ``management`` (everything but use and refused
        use: creation, drafts, publishing, sharing, forks, archiving, unpins);
        ``use``; ``denied`` (refused use, one counted row per actor and hour).
        Management events can always be paged to, however many uses happen.
        """
        if category not in AUDIT_CATEGORIES:
            raise PromptValidationError(f"category must be one of {', '.join(AUDIT_CATEGORIES)}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_AUDIT_PAGE_SIZE:
            raise PromptValidationError(f"limit must be between 1 and {MAX_AUDIT_PAGE_SIZE}")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PromptValidationError("offset must be zero or more")
        await self._authorize(user_email, prompt_id, LEVEL_MANAGE)
        include, exclude = {
            "all": (None, None),
            "management": (None, ["use", "use_denied"]),
            "use": (["use"], None),
            "denied": (["use_denied"], None),
        }[category]
        page = await self._db(self._repo.page_audit_events, prompt_id, limit, offset, include, exclude)
        return {**page, "limit": limit, "offset": offset, "category": category}

    async def shareable_groups(self, user_email: str) -> List[str]:
        """Configured groups the caller belongs to (the only ones they may share with)."""
        return (await self.shareable_groups_checked(user_email))["groups"]

    async def shareable_groups_checked(self, user_email: str) -> Dict[str, Any]:
        """``{groups, incomplete}``: sharing targets, with the checks bounded.

        At most ``MAX_SHAREABLE_GROUP_CHECKS`` configured groups are checked
        (in name order) within ``SHAREABLE_GROUP_CHECK_TIMEOUT_S``. Only
        groups whose check completed as "member" are returned; when a bound
        cut the checks short, ``incomplete`` is true and the rest are simply
        not offered (the server checks membership again on every sharing
        change regardless).
        """
        user_email = normalize_user_email(user_email)
        if not user_email:
            return {"groups": [], "incomplete": False}
        candidates = sorted({g for g in self._group_candidates() if g and not is_org_wide_group(g)})
        within_cap = len(candidates) <= MAX_SHAREABLE_GROUP_CHECKS
        if not within_cap:
            logger.warning(
                "Shareable-group lookup checked only %d of the configured groups", MAX_SHAREABLE_GROUP_CHECKS
            )
            candidates = candidates[:MAX_SHAREABLE_GROUP_CHECKS]
        members, finished = await self._member_groups_within(
            user_email, candidates, SHAREABLE_GROUP_CHECK_TIMEOUT_S
        )
        if not finished:
            logger.warning("Shareable-group checks timed out; unchecked groups are not offered")
        return {"groups": sorted(members), "incomplete": not (within_cap and finished)}

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
        prompt = await self._db(
            self._repo.create_prompt, user_email, title, description, content,
            max_owned=self._limit("prompts_per_owner"),
        )
        await self._audit(prompt["id"], "create", user_email)
        return await self.get_prompt(user_email, prompt["id"])

    async def update_prompt(
        self,
        user_email: str,
        prompt_id: str,
        expected_draft_revision: Any,
        title: Optional[str] = None,
        description: Optional[str] = None,
        content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Change the draft, if it is still at ``expected_draft_revision``.

        The response describes the draft *this* call wrote (or confirmed):
        its ``draft_content`` and ``draft_revision`` come from the snapshot the
        compare-and-set produced, never from a later read. The editor then
        publishes with that revision, so a save-and-publish publishes exactly
        what its user saved -- or gets a conflict if anyone saved in between.
        """
        expected = check_revision(expected_draft_revision, "expected_draft_revision")
        user_email, prompt, access = await self._authorize(user_email, prompt_id, LEVEL_EDIT)
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
        if not changed:
            if prompt["draft_revision"] != expected:
                raise PromptConflict("draft")
            return await self._detail_view(prompt_id, prompt, access)
        # The edit right is re-checked inside the write's transaction (under
        # the row lock sharing changes also take), not only above.
        updated = await self._db(
            self._repo.update_prompt, prompt_id, expected, title, description, content, user_email
        )
        if updated is None:
            raise PromptNotFound()
        await self._audit(
            prompt_id, "update_draft", user_email,
            details={"fields": changed, "draft_revision": updated["draft_revision"]},
        )
        return await self._detail_view(prompt_id, updated, access)

    async def publish(
        self,
        user_email: str,
        prompt_id: str,
        expected_draft_revision: Any,
        change_description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Publish the draft the caller reviewed (``expected_draft_revision``)."""
        expected = check_revision(expected_draft_revision, "expected_draft_revision")
        user_email, _, _ = await self._authorize(user_email, prompt_id, LEVEL_EDIT)
        if change_description is not None and len(change_description) > MAX_CHANGE_DESCRIPTION_LEN:
            raise PromptValidationError(
                f"Change description is longer than {MAX_CHANGE_DESCRIPTION_LEN} characters"
            )
        version = await self._db(
            self._repo.publish_version, prompt_id, user_email, change_description, expected,
            True, self._limit("versions_per_prompt"),
        )
        if version is None:
            raise PromptNotFound()
        await self._audit(
            prompt_id, "publish", user_email, version_id=version["id"],
            details={"version_number": version["version_number"], "draft_revision": expected},
        )
        return version_view(version, include_author_email=True)

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
            source = await self._db(self._repo.get_version, prompt_id, version_id)
        else:
            source = await self._db(self._repo.get_latest_version, prompt_id)
        if source is None:
            raise PromptNotFound()
        new_title = clean_title(title, required=False) or f"{source['title']} (copy)"[:200]
        new_prompt = await self._db(
            self._repo.create_prompt, user_email, new_title, prompt.get("description"), source["content"],
            prompt_id, source["id"], max_owned=self._limit("prompts_per_owner"),
        )
        await self._audit(
            prompt_id, "fork", user_email, version_id=source["id"],
            details={"new_prompt_id": new_prompt["id"], "version_number": source["version_number"]},
        )
        await self._audit(
            new_prompt["id"], "create", user_email,
            details={"forked_from_prompt_id": prompt_id, "forked_from_version_id": source["id"]},
        )
        return await self.get_prompt(user_email, new_prompt["id"])

    async def archive(self, user_email: str, prompt_id: str) -> None:
        """Hide the entry and stop new use. Versions are kept for history."""
        user_email, _, _ = await self._authorize(user_email, prompt_id, LEVEL_OWNER)
        if not await self._db(self._repo.archive_prompt, prompt_id):
            raise PromptNotFound()
        await self._audit(prompt_id, "archive", user_email)

    # ------------------------------------------------------------------
    # Chat execution
    # ------------------------------------------------------------------

    async def resolve_for_execution(
        self,
        user_email: str,
        prompt_id: str,
        version_id: Optional[str] = None,
        record_denial: bool = True,
    ) -> Dict[str, Any]:
        """The exact published version a chat turn may run, after a fresh check.

        ``version_id`` None means the latest published version. Raises
        ``PromptUnavailable`` -- one message for every reason -- when the entry
        is missing, archived, not visible to this user (now), or the version
        does not belong to it. Never falls back to another version.

        ``record_denial`` is False for the advisory ``/resolve`` endpoint,
        which must stay read-only; a chat turn records ``use_denied``.
        """
        user_email = normalize_user_email(user_email)
        if not user_email or not prompt_id:
            raise PromptUnavailable()
        prompt = await self._db(self._repo.get_prompt, prompt_id)
        if prompt is None:
            raise PromptUnavailable()
        grants = await self._db(self._repo.list_grants, prompt_id)
        unknown: Set[str] = set()
        access = await self._access(user_email, prompt, grants, unknown_groups=unknown, first_only=True)
        if access.level == LEVEL_NONE and unknown and not prompt.get("archived_at"):
            # Not a confirmed denial: a group grant could not be checked. Not
            # recorded as a refused use; the caller sees the same message.
            raise PromptMembershipUnknown()
        if access.level == LEVEL_NONE:
            # Recorded for an entry that exists (archived or revoked); ids that
            # match nothing leave no row behind.
            if record_denial:
                # One counted row per actor, prompt and window: repeated
                # refused turns are evidence, but cannot flood the trail.
                await self._audit(prompt_id, "use_denied", user_email, version_id,
                                  dedup_window_s=USE_DENIED_DEDUP_WINDOW_S)
            raise PromptUnavailable()
        if version_id:
            version = await self._db(self._repo.get_version, prompt_id, version_id)
        else:
            version = await self._db(self._repo.get_latest_version, prompt_id)
        if version is None:
            raise PromptUnavailable()
        return version

    async def record_use(
        self, user_email: str, version: Dict[str, Any], conversation_id: Optional[str]
    ) -> None:
        """Audit the moment a conversation is pinned to a version."""
        await self._audit(
            version["prompt_id"], "use", user_email, version["id"],
            {"version_number": version["version_number"], "conversation_id": conversation_id},
        )

    async def record_unpin(self, user_email: str, prompt_id: str, conversation_id: str) -> None:
        """Audit an explicit clear of a conversation's pinned prompt."""
        if await self._db(self._repo.get_prompt, prompt_id) is not None:
            await self._audit(prompt_id, "unpin", user_email, details={"conversation_id": conversation_id})
