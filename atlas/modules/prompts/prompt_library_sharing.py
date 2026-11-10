"""Sharing for the prompt library (issue #1169).

A mixin of ``PromptLibraryService``. Sharing changes are optimistic: the
caller names the sharing revision its desired grants were computed from; the
authorization checks run against the grants at that revision, and the
repository refuses the write if they changed since (see
``prompt_library_grants``), so a stale update can never restore a revoked
grant.
"""

from typing import Any, Dict, Sequence

from atlas.modules.chat_history.prompt_library_repository import GRANTEE_GROUP, GRANTEE_USER
from atlas.modules.prompts.prompt_library_policy import (
    LEVEL_MANAGE,
    LEVEL_OWNER,
    PromptConflict,
    PromptForbidden,
    PromptNotFound,
    check_revision,
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
