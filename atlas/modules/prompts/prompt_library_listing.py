"""Paginated, authorization-first listing for the prompt library (issue #1169).

A mixin of ``PromptLibraryService``: visibility is decided from id-level
grant data before any prompt row is read, then the database filters,
searches, counts and pages that authorized set. The page is rendered with
one query for its grants and one for its latest versions -- no per-entry
queries.
"""

from typing import Any, Dict, List, Optional, Set

from atlas.core.user_identity import normalize_user_email
from atlas.modules.chat_history.prompt_library_repository import GRANTEE_GROUP
from atlas.modules.prompts.prompt_library_policy import (
    DEFAULT_PAGE_SIZE,
    LEVEL_EDIT,
    LEVEL_NONE,
    MAX_PAGE_SIZE,
    PERMISSION_LEVELS,
    SCOPES,
    PromptValidationError,
    summary_view,
)


class PromptLibraryListing:
    """``list_prompts`` for ``PromptLibraryService``."""

    async def _label_member_groups(self, user_email: str, groups: Set[str]) -> Set[str]:
        """The caller's groups among ``groups``, for route labels only.

        Bounded like the team listing (``MAX_LISTING_GROUP_CHECKS`` groups,
        ``LISTING_GROUP_CHECK_TIMEOUT_S``); unchecked groups are not returned.
        """
        from atlas.modules.prompts import prompt_library_service as svc

        names = sorted(groups)[: svc.MAX_LISTING_GROUP_CHECKS]
        if not names:
            return set()
        member, _finished = await self._member_groups_within(user_email, names, svc.LISTING_GROUP_CHECK_TIMEOUT_S)
        return member

    async def list_prompts(
        self,
        user_email: str,
        scope: str = "all",
        query: Optional[str] = None,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """One page of the entries this user may see, plus the total.

        scope: ``mine`` (owned), ``shared`` (direct user grant), ``team``
        (through a group the user is in), ``all`` (union). Visibility is
        decided from id-level grant data first; the database then filters,
        searches, counts and pages that authorized set, and the page is
        rendered with one query each for grants and latest versions.

        Group membership checks are bounded (count and time). When a bound
        cuts them short the page carries ``"incomplete": true`` and omits the
        entries only those unchecked groups would have shown.
        """
        if scope not in SCOPES:
            raise PromptValidationError(f"scope must be one of {', '.join(SCOPES)}")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
            raise PromptValidationError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise PromptValidationError("offset must be zero or more")
        user_email = normalize_user_email(user_email)
        empty = {"prompts": [], "total": 0, "limit": limit, "offset": offset}
        if not user_email:
            return empty

        owned = set(await self._db(self._repo.list_owned_prompt_ids, user_email))
        direct = await self._db(self._repo.prompt_ids_granted_to_user, user_email)
        member_groups: Set[str] = set()
        team: Dict[str, List[str]] = {}
        complete = True
        if scope in ("all", "team"):
            # Bounded in group count and time; see _listing_member_groups.
            member_groups, complete = await self._listing_member_groups(user_email)
            team = await self._db(self._repo.prompt_ids_granted_to_groups, member_groups)

        if scope == "mine":
            candidates = set(owned)
        elif scope == "shared":
            candidates = set(direct) - owned
        elif scope == "team":
            candidates = set(team) - owned
        else:
            candidates = owned | set(direct) | set(team)
        # Owners and editors see an entry whatever its state; use-only callers
        # only once something is published (enforced in the query).
        full_access = {
            p for p in candidates
            if p in owned or PERMISSION_LEVELS.get(direct.get(p, ""), LEVEL_NONE) >= LEVEL_EDIT
        }
        total, prompts = await self._db(
            self._repo.page_prompts, full_access, candidates - full_access, query, limit, offset
        )
        page_ids = [p["id"] for p in prompts]
        grants_by_prompt = await self._db(self._repo.grants_for_prompts, page_ids)
        latest_by_id = await self._db(
            self._repo.versions_by_id,
            [p["latest_version_id"] for p in prompts if p.get("latest_version_id")],
            False,  # metadata only: a list never needs version text
        )
        if scope not in ("all", "team"):
            # Here visibility never depends on groups: "mine" is ownership and
            # "shared" a direct grant, which is always at least ``use`` -- the
            # most a group can give. Membership only labels the routes, so it
            # is checked for entries the caller does not own, within the same
            # bounds as the team listing; a group whose check did not finish
            # is not claimed as a route (fail closed), and nothing is omitted.
            member_groups = await self._label_member_groups(
                user_email,
                {
                    g["grantee"]
                    for p in prompts if p["id"] not in owned
                    for g in grants_by_prompt.get(p["id"], []) if g["grantee_type"] == GRANTEE_GROUP
                },
            )

        results = []
        for prompt in prompts:
            grants = grants_by_prompt.get(prompt["id"], [])
            access = await self._access(user_email, prompt, grants, member_groups)
            if access.level == LEVEL_NONE:  # membership changed mid-request
                continue
            results.append(summary_view(prompt, access, grants, latest_by_id.get(prompt.get("latest_version_id"))))
        page = {"prompts": results, "total": total, "limit": limit, "offset": offset}
        if not complete:
            # Some team entries may be missing: the group checks were cut short.
            page["incomplete"] = True
        return page
