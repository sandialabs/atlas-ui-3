"""Sharing for the prompt library (issue #1169).

A mixin of ``PromptLibraryService``. Sharing changes are optimistic: the
caller names the sharing revision its desired grants were computed from; the
authorization checks run against the grants at that revision, and the
repository refuses the write if they changed since (see
``prompt_library_grants``), so a stale update can never restore a revoked
grant.
"""

from typing import Any, Dict, Sequence, Set

from atlas.modules.chat_history.prompt_library_repository import GRANTEE_GROUP, GRANTEE_USER
from atlas.modules.prompts.prompt_library_policy import (
    LEVEL_MANAGE,
    LEVEL_OWNER,
    GroupCheckUnavailable,
    PromptConflict,
    PromptForbidden,
    PromptNotFound,
    check_revision,
    is_org_wide_group,
    parse_grants,
    sharing_view,
)


class PromptLibrarySharing:
    """``get_sharing`` / ``set_sharing`` for ``PromptLibraryService``."""

    async def get_sharing(self, user_email: str, prompt_id: str) -> Dict[str, Any]:
        await self._authorize(user_email, prompt_id, LEVEL_MANAGE)
        state = await self._db(self._repo.get_sharing_state, prompt_id)
        if state is None:
            raise PromptNotFound()
        revision, grants = state
        return sharing_view(grants, revision)

    async def _check_added_groups(self, user_email: str, added_groups: Set[str]) -> None:
        """Refuse a newly added group unless it is configured and the caller's.

        Only groups Atlas is configured with (the sharing-target candidates)
        are ever sent to the authorizer: an arbitrary client-supplied name is
        refused without a check. Configured groups are checked within the
        same count and time bounds as the offered list
        (``_member_groups_within``); a check that times out or fails is
        "unknown", which refuses the change rather than counting as "yes".
        The configured groups in the request are checked first, and an
        unfinished check refuses the whole change as "unknown" (503) whether or
        not unconfigured names are also present; after that, a name that is
        not configured and one the caller is not in get the same 403 and
        message. So the status code never tells a caller which names are
        configured. (Response time still differs, since unconfigured names
        never reach the authorizer; that is the price of not sending arbitrary
        strings to it.) Existing grants are not re-checked: removing a group
        never needs the authorizer.
        """
        from atlas.modules.prompts import prompt_library_service as svc

        configured = {g for g in self._group_candidates() if g and not is_org_wide_group(g)}
        to_check = sorted(added_groups & configured)
        member, finished = (
            await self._member_groups_within(user_email, to_check, svc.SHAREABLE_GROUP_CHECK_TIMEOUT_S)
            if to_check else (set(), True)
        )
        if not finished and set(to_check) - member:
            raise GroupCheckUnavailable()
        not_member = added_groups - (member & configured)
        if not_member:
            raise PromptForbidden(
                "You can only share with groups you belong to: " + ", ".join(sorted(not_member))
            )

    async def set_sharing(
        self,
        user_email: str,
        prompt_id: str,
        groups: Sequence[str],
        users: Sequence[Dict[str, str]],
        expected_revision: Any,
    ) -> Dict[str, Any]:
        """Replace the entry's grants, if they are still at ``expected_revision``.

        Rules: groups get ``use`` only; a newly added group must be one the
        caller belongs to (no sharing into groups you are not part of); the
        everyone group is refused; user grants may be ``use``/``edit``/
        ``manage`` but only the owner may add, change or remove ``manage``;
        the owner is never a grantee. The checks run against the grants at
        ``expected_revision``, and the write is refused if they changed since.
        """
        expected = check_revision(expected_revision, "expected_revision")
        user_email, prompt, access = await self._authorize(user_email, prompt_id, LEVEL_MANAGE)
        state = await self._db(self._repo.get_sharing_state, prompt_id)
        if state is None:
            raise PromptNotFound()
        revision, current = state
        if revision != expected:
            raise PromptConflict("sharing")
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
            await self._check_added_groups(user_email, added_groups)

        grants = [
            {"grantee_type": GRANTEE_GROUP, "grantee": g, "permission": "use"} for g in sorted(new_groups)
        ] + [
            {"grantee_type": GRANTEE_USER, "grantee": e, "permission": p} for e, p in sorted(new_users.items())
        ]
        stored = await self._db(self._repo.replace_grants, prompt_id, grants, user_email, expected)
        if stored is None:
            raise PromptNotFound()
        new_revision, stored_grants = stored

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
            await self._audit(prompt_id, "share", user_email, details={**details, "sharing_revision": new_revision})
        return sharing_view(stored_grants, new_revision)
