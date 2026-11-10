# Team Prompt Library (issue #1169)

Date: 2026-11-09 (review revisions 2026-11-10, third review 2026-11-10)

How the team-shared, versioned prompt library was built, and why. User-facing
behaviour is in the [user guide](../../user-guide/team-prompt-library/README.md).

## Shape

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Storage | `atlas/modules/chat_history/prompt_library_repository.py`, `prompt_library_grants.py`, `prompt_library_storage.py` | Rows only; no authorization decisions; typed storage errors |
| Policy | `atlas/modules/prompts/prompt_library_policy.py` | Levels, errors, validation, per-level response views |
| Service | `atlas/modules/prompts/prompt_library_service.py` (+ `prompt_library_listing.py`, `prompt_library_sharing.py`) | Every access decision; audit; all storage calls in worker threads |
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
- Group names are stored and passed to `is_user_in_group` exactly as
  configured (only surrounding whitespace is dropped): an external authorizer
  receives the raw name and may match case-sensitively. Only the reserved
  everyone group is compared case-insensitively.
- `users` (the group `is_user_in_group` grants every identity when no
  authorizer is configured) is reserved (`ORG_WIDE_GROUPS`): never offered,
  refused as a grant, and ignored in access evaluation if a row exists, so
  the MVP cannot become an organization-wide catalog.
- List/search first computes the authorized id set, then narrows it with the
  search term, so a search can never widen visibility. For the Team scope the
  granted groups are checked once per request, bounded three ways: 8 checks
  at a time, at most 100 groups (the most-granted on live prompts first),
  and 10 seconds for the whole pass. Past a bound the page carries
  `"incomplete": true` and leaves out entries only the unchecked groups
  would show (fail closed); the panel says so. Using an entry by reference
  is unaffected -- its own grants are checked.
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
| names an entry **and** inline/persona | any | rejected (`ValidationError`), nothing looked up |

A frame that names a library entry together with an inline custom prompt or a
persona is ambiguous, so it is rejected with "Choose either a team library
prompt or a custom prompt, not both." -- on the raw WebSocket frame before
either side is resolved (`frame_has_conflicting_prompts`, error type
`validation`), and again in the service for other callers (inline prompt or
`persona_id`). No library lookup
happens, so no `use` or `use_denied` is audited; the pin is untouched.

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

### Pin state is the stored record's

`atlas.domain.conversation_prompt_pin` owns the stored rules:

- `metadata.prompt_library_pin_epoch` counts explicit clears. The clear bumps
  it under the conversation's row lock (`SELECT ... FOR UPDATE` on
  PostgreSQL), even when no pin is stored, because a session may hold one it
  has not saved yet.
- Every save reads the record under the same row lock and reconciles: a pin
  sent with an older epoch than the stored one is dropped, the stored epoch
  is kept, and stored pin history the save lacks is carried over. So a
  session that was open during a clear can never write the pin back,
  whichever way its save and the clear interleave.
- Before a turn that is pinned, selects a library entry, or has an unknown
  pin state, the session re-reads the record's metadata (owner-filtered, no
  messages) and adopts the stored pin when the epoch moved. A clear therefore
  takes effect on an already-open session's next turn, and re-choosing the
  entry afterwards pins its current latest under the new epoch.
- When a conversation fails to load and the session was not already carrying
  it, the pin is *unknown*, never "none": the turn re-reads the metadata and
  runs the stored pin (re-authorized), or is refused with "could not be
  loaded right now ... try again" when the store cannot answer. This holds
  with compliance enforcement on or off, so a failed load never swaps a
  pinned prompt for the default. A session in that state saves
  `KEEP_STORED_PIN` instead of pin keys, which carries the stored pin,
  history and epoch through untouched. "No record" from the re-read does not
  settle an unknown pin (the full load failed, so it is not proof the
  conversation is new): the turn is refused, and every following turn
  retries the full load until it answers -- a genuinely new conversation
  then proceeds with no pin. A failed reload of the conversation
  the session already carries keeps its own pin, which the re-read then
  checks against the store.

## Recovering from an unavailable pin

Clients that never send `library_prompt` -- the CLI, follow-up and Agent Runs
API turns, older browser bundles -- keep running a pinned conversation's
version. When that version is no longer available they are refused (never
silently switched to another prompt) with a message naming the explicit way
out: `DELETE /api/prompts/pins/{conversation_id}`, owner only, which removes
`metadata.prompt_library` (the history is kept) and records an `unpin` audit
event. The next run or connection hydrates the conversation without a pin and
uses the default prompt. A storage outage during resolution is reported as
such and keeps the pin. Browsers choose explicitly every turn
(`library_prompt: null` unpins).

## Concurrency and scaling

- Version numbers are unique per prompt (database constraint). Publishing
  computes `max+1` in a transaction (`SELECT ... FOR UPDATE` on the prompt row
  on PostgreSQL) and retries with jittered backoff only on transient
  conflicts: a unique violation of that constraint, a PostgreSQL serialization
  failure/deadlock/lock timeout, or a DuckDB write conflict. Any other
  database error fails at once; exhausted retries end in a typed conflict
  (HTTP 409), never an unhandled exception.
- Drafts carry `draft_revision`; saving is a compare-and-set on it and
  publishing names the revision the editor reviewed, so concurrent editors get
  a 409 instead of overwriting each other or publishing unseen text. The save
  response is the row as *that* write left it, read inside the compare-and-set
  transaction (under its row lock on PostgreSQL), never a later read: the
  editor's save-then-publish names this save's revision, so a save that lands
  in between makes the publish fail instead of publishing it. The
  compare-and-set tests the `UPDATE ... RETURNING` rows, not `rowcount`,
  which DuckDB's driver reports as -1.
- Grants carry `sharing_revision`. A sharing change is an `UPDATE ... WHERE
  sharing_revision = expected` (which takes the prompt's row lock on
  PostgreSQL) followed by a diff of the grant rows, all in one transaction;
  a stale or concurrent change is refused, so it cannot restore a grant that
  was just revoked. `UNIQUE (prompt_id, grantee_type, grantee)` backs it up.
  Writing a diff (never delete-then-reinsert of one key) keeps DuckDB's
  constraint checking happy.
- The service runs every repository call with `asyncio.to_thread`; each call
  opens and closes its own session in that thread, so publish backoff or a
  slow database never stalls the event loop.
- Listing decides visibility from id-level grant data, then the database
  filters, searches (LIKE wildcards escaped), counts and pages; a page is
  rendered with one query each for grants and latest versions.
- Database exception text (SQL, parameters, prompt text, identities) never
  reaches a log or a response: storage errors are logged as operation,
  exception type and SQLSTATE, and re-raised unchained as a typed error.
- No process-local state: PostgreSQL is the source of truth for entries,
  versions, grants, audit and pins; membership comes from the authorizer on
  each request. Migration `004` runs under the existing advisory-locked
  migration runner, so no deployment change is needed.

## Audit

`prompt_library_audit_events` records `create`, `update_draft` (field names),
`publish` (version number), `share` (groups/users added, removed, changed),
`fork`, `archive`, `use` (once per pin, with conversation id), `use_denied`
(a refused chat turn; the advisory `/resolve` endpoint writes nothing) and
`unpin`.
Prompt text is never written to audit rows or logs.

## Tests

`atlas/tests/test_prompt_library_access.py`, `..._routes.py`,
`..._chat_turn.py`, `..._migration.py`, `..._concurrency.py`,
`..._storage.py`, `atlas/tests/test_conversation_prompt_pin.py` (SQLite,
DuckDB and, with `ATLAS_TEST_POSTGRES_URL`, PostgreSQL); frontend `src/test/prompt-library-*.test.*`;
end-to-end `test/pr-validation/test_pr1169_team_prompt_library.sh`.
