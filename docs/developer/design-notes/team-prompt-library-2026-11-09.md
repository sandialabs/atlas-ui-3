# Team Prompt Library (issue #1169)

Date: 2026-11-09

How the team-shared, versioned prompt library was built, and why. User-facing
behaviour is in the [user guide](../../user-guide/team-prompt-library/README.md).

## Shape

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Storage | `atlas/modules/chat_history/prompt_library_repository.py` | Rows only; no authorization decisions |
| Policy | `atlas/modules/prompts/prompt_library_policy.py` | Levels, errors, validation, per-level response views |
| Service | `atlas/modules/prompts/prompt_library_service.py` | Every access decision; audit |
| REST | `atlas/routes/prompt_library_routes.py` | `/api/prompts`, identity from `get_current_user` |
| Chat | `atlas/application/chat/prompt_library_turn.py` | Per-turn resolution and version pinning, called from `ChatService.handle_chat_message` |
| UI | `usePromptLibrary`, `PromptLibraryPanel` (+ Editor/Sharing/Versions), `PromptSelector`, `utils/libraryPromptSelection.js` | Browse, manage, select; send a reference only |

The library is a separate registry rather than a conversion of `user_prompts`:
issue #153 prompts keep their API, storage and inline execution unchanged, and
"Save to team library" copies one in explicitly. That keeps the migration
purely additive and leaves no silent change in what an existing private prompt
does.

## Authorization

Atlas's existing group check (`atlas.core.auth.is_user_in_group`) is the only
membership source; the library adds an object ACL, not a new identity or group
model.

- Levels: `use` < `edit` < `manage` < `owner`. Group grants are always `use`;
  `edit`/`manage` exist only as direct user grants; only the owner changes
  `manage` grants.
- Effective level is the maximum over all applicable grants (union).
- Membership is never cached by the library; every request (and chat turn)
  asks again, so revocation applies to the next request on every replica.
- A group check that raises is "not a member" (fail closed). The real
  `is_user_in_group` already returns `False` on an authorizer error.
- Callers with no access get the same `PromptNotFound` as a missing id; 403 is
  only for callers who can already see the entry.
- Sharing into a group requires the sharer to be a member of it.
- `users` (the group `is_user_in_group` grants every identity when no
  authorizer is configured) is reserved (`ORG_WIDE_GROUPS`): never offered,
  refused as a grant, and ignored in access evaluation if a row exists, so
  the MVP cannot become an organization-wide catalog.
- List/search first computes the authorized id set, then narrows it with the
  search term, so a search can never widen visibility. For the Team scope the
  distinct granted groups are checked once per request (bounded concurrency).
- A use-only caller does not see an entry until something is published.

## Execution and pinning

The chat frame carries `library_prompt: {prompt_id, version_id|null}`. The
service resolves it after the turn's admission checks (classification, tools,
data sources, which still run first and unchanged) and after the conversation
is hydrated, using the server-authenticated user. Outcomes:

| Frame | Conversation pinned to same entry | Result |
|-------|-----------------------------------|--------|
| explicit `version_id` | any | that version (if authorized); re-pin |
| `version_id: null` | yes | keep the pinned version |
| `version_id: null` | no | current latest; pin it |
| `library_prompt: null` | any | unpin; inline/persona/default applies |
| omitted | yes, no other prompt in frame | pinned version, re-authorized |
| omitted | yes, inline/persona in frame | unpin; that prompt applies |

Any refusal raises `AuthorizationError` before the turn runs or is saved, with
one generic message. It never falls back to another version or the default
prompt; the client sends `library_prompt: null` to move on explicitly.

The pin (`metadata.prompt_library`) and a capped change history
(`metadata.prompt_library_history`, with the message index each version took
effect at) are written by `_save_conversation` from session context only, and
loaded back on hydration and sidebar restore. In the client, a workspace restore triggered by
opening a pinned conversation (immediate or deferred) re-asserts the pinned
key afterwards; an explicit workspace switch by the user does not. Because versions are insert-only
and archiving is a soft delete, a pinned id always resolves to its original
text; running it again still needs current access. That is the distinction
between preserving history and permitting new use.

## Concurrency and scaling

- Version numbers are unique per prompt (database constraint). Publishing
  computes `max+1` in a transaction (`SELECT ... FOR UPDATE` on the prompt row
  on PostgreSQL) and retries with jittered backoff when a concurrent publisher
  wins; DuckDB's optimistic write conflicts are retried the same way.
- Grant changes replace the whole set in one transaction.
- No process-local state: PostgreSQL is the source of truth for entries,
  versions, grants, audit and pins; membership comes from the authorizer on
  each request. Migration `004` runs under the existing advisory-locked
  migration runner, so no deployment change is needed.

## Audit

`prompt_library_audit_events` records `create`, `update_draft` (field names),
`publish` (version number), `share` (groups/users added, removed, changed),
`fork`, `archive`, `use` (once per pin, with conversation id) and `use_denied`.
Prompt text is never written to audit rows or logs.

## Tests

`atlas/tests/test_prompt_library_access.py`, `..._routes.py`,
`..._chat_turn.py`, `..._migration.py` (SQLite, DuckDB and, with
`ATLAS_TEST_POSTGRES_URL`, PostgreSQL); frontend `src/test/prompt-library-*.test.*`;
end-to-end `test/pr-validation/test_pr1169_team_prompt_library.sh`.
