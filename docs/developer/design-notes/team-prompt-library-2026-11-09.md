# Team Prompt Library (issue #1169)

Date: 2026-11-09 (review revisions 2026-11-10, 2026-11-13)

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
- Sharing into a group requires the sharer to be a member of it, and the
  group must be one of the configured sharing candidates (the same list
  `GET /api/prompts/groups` draws from). A name outside that list is refused
  (403) before the authorizer is asked, so an arbitrary string never reaches
  it. A configured group whose membership check failed or timed out
  (`SHAREABLE_GROUP_CHECK_TIMEOUT_S`) is refused with 503 "could not be
  checked", not a 403: sharing is unchanged and the caller can retry.
  Only groups being added are checked; existing grants are kept as they are.
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
  and 10 seconds for the whole pass. A check whose authorizer call failed
  counts as unchecked, not as "no". Past a bound, or after a failed check,
  the page carries `"incomplete": true` and leaves out entries only the
  unchecked groups would show (fail closed); the panel says so, and the
  client never concludes from an incomplete listing that a selected entry
  was revoked -- it sends the reference and the server decides. Using an
  entry by reference is unaffected: its own grants are checked.
- In the Mine and Shared scopes visibility never depends on groups (Mine is
  ownership; Shared is a direct grant, always at least `use`, the most a
  group can give), so groups only label the routes. Entries the caller owns
  are never group-checked; the others' groups are checked within the same
  count and time bounds, and a group whose check did not finish is not shown
  as a route. Nothing is omitted, so these pages are never `incomplete`.
- The groups offered as sharing targets (`GET /api/prompts/groups`) are
  bounded the same way (100 configured groups in name order, 10 seconds).
  Only groups whose check completed as "member" are offered; the response
  says `"incomplete": true` when some were not checked, and the sharing
  editor says so. Adding a group to a prompt re-checks membership regardless.
- A use-only caller does not see an entry until something is published.
- Archiving hides an entry from listing, detail, editing, sharing and new
  chat selections for everyone. Its *history* stays readable: version
  summaries, a single version's content and export (`archived: true`) remain
  available to anyone whose grants still allow `use`, because those versions
  are immutable and conversations pinned to them keep referring to them. A
  revoked caller gets the same not-found as before; history access never
  creates or widens access. A conversation pinned to an archived prompt is
  refused (never silently run on another prompt) and keeps its pin history;
  the owner can clear the pin to continue.

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

`atlas.domain.conversation_prompt_pin` owns the stored rules.
`metadata.prompt_library_pin_revision` counts every change to the pin: a set,
a re-pin to another version, an explicit clear (`library_prompt: null`, or
another prompt chosen while pinned) and the owner's `DELETE
/api/prompts/pins/{conversation_id}`.

Conflict resolution:

- **Pin changes are compare-and-set.** A turn that changes the pin does it
  before the turn runs, with `ConversationRepository.change_prompt_pin`: under
  the conversation's row lock (`SELECT ... FOR UPDATE` on PostgreSQL) it
  applies the change only if the stored revision equals the one the change was
  decided on, appends a history entry and bumps the revision. On DuckDB, which
  has no row locks, a concurrent writer fails its commit instead.
- **The revision a change is decided on** is the frame's optional
  `library_prompt_revision` (the browser sends the revision it last saw: from
  the loaded conversation, `conversation_saved` frames and conflict errors),
  else the session's own. If the stored revision moved since, the change is
  refused with error type `prompt_pin_conflict`; the error carries the stored
  pin and revision, the browser shows that selection, and the user sends
  again. Nothing runs and nothing is audited for the refused turn. The error
  frame names its `conversation_id` (and, through the run envelope, the
  `run_id`); the browser applies it only to the conversation it names and
  ignores a conflict for a conversation it is no longer showing.
- **`conversation_saved` reports what was committed.** The frame's
  `prompt_library` and `prompt_library_pin_revision` come from the record the
  save just wrote (the save carries the stored pin, so this is the pin another
  window may have set or cleared meanwhile), not from the session's earlier
  view. If the saved record's pin cannot be read, the frame carries no pin
  fields at all rather than a guess; the browser never treats their absence
  as "default prompt".
- **Saves never write the pin of an existing conversation.** A save carries
  the stored pin, history and revision through whatever the saving session
  holds, so a session that never saw the current pin (an older tab, a
  reconnect, a run started earlier) cannot overwrite or remove it by saving.
  A save that *creates* the record writes the creating session's pin.
- **Turns that keep the pin follow the store.** Every turn of a stored
  conversation re-reads its metadata (owner-filtered, no messages) first, so
  a pin set, changed or cleared elsewhere applies to an open session's next
  turn, and a turn that omits `library_prompt` runs the stored pin
  (re-authorized).
- **The owner's DELETE is unconditional**: it is the explicit way out when a
  pinned prompt became unavailable.
- **Off-record turns change nothing stored.** An incognito turn in a stored
  conversation runs its selection but writes no pin and leaves the session's
  view of the stored one alone.

Unknown and unreadable state:

- When a conversation fails to load and the session was not already carrying
  it, the pin is *unknown*, never "none". The turn re-reads the metadata and
  runs the stored pin, or is refused with "could not be loaded right now ...
  try again" when the store cannot answer. "No record" from that re-read does
  not settle it (the full load failed, so it is not proof the conversation is
  new): the turn is refused and every following turn retries the full load
  until it answers; a genuinely new conversation then proceeds with no pin.
- Stored metadata that is not a JSON object (malformed JSON, a list, a
  string, a number) raises `ConversationMetadataUnreadableError` instead of
  reading as `{}`, so it never looks like "no pin, revision 0". Turns are
  refused, and no turn or save rewrites the blob (the classification guard
  also refuses saves over it). The same holds for valid JSON whose pin or pin
  revision is malformed (a pin without non-blank string `prompt_id` and
  `version_id`, or a revision that is not a non-negative integer): it is not
  read as "no pin" or revision 0, saves over it are refused, and the pin
  state is unknown. An empty blob, JSON `null`, absent pin keys and
  `prompt_library: null` are genuinely empty. A refused turn says the stored
  settings are damaged and names the owner's way out (below); a store that
  cannot answer still says "try again".
- **Recovery is the owner's explicit DELETE** (`recover_unreadable_metadata`,
  under the row lock). It never guesses a pin and changes nothing else:
  - a JSON object keeps every other key as stored (data classification,
    workspace, origin, lineage); the pin is removed, readable history entries
    stay, and the damaged values are kept under `prompt_library_recovery`;
  - anything else cannot be read at all, so nothing in it is trusted: the
    result holds only the quarantined original blob and an *unknown*
    classification record, which still reads as `invalid` -- the
    conversation remains unusable for chat (as before) until an operator
    decides what it holds;
  - the revision becomes the old one + 1 when it was readable, otherwise at
    least the current Unix time in seconds, so no revision a client saw
    before the damage can match it;
  - the response is `{"cleared": true, "recovered": true}`; nothing from the
    damaged pin is audited or logged, and other users still get 404;
  - `prompt_library_recovery` is server-written: saves carry it like the
    pin, and a new conversation cannot be created with one.
- The live view of a run that has not saved yet (`in_flight`) carries the
  same pin, history and revision as the session, so a tab that opens it shows
  the right selection and does not send `library_prompt: null` by accident.

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
  rendered with one query each for grants and latest versions. Listing never
  loads prompt text: the draft body is deferred, "has unpublished changes" is
  computed in SQL against the latest version, and latest-version summaries
  are loaded without their content. Detail and single-version reads still
  return text.
- Version history is paged (`limit` 1-200, default 50, newest first) and
  lists summaries without prompt text; the text is not even loaded from the
  database. `GET .../versions/{version_id}` returns one version's content.
  An export holds at most 100 versions with content per response and says
  `versions_truncated` (continue with `offset`).
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
`..._storage.py`, `..._bounds.py`, `..._archived.py`, `..._listing_columns.py`,
`atlas/tests/test_prompt_pin_conflict_frames.py`,
`atlas/tests/test_conversation_prompt_pin.py` (SQLite,
DuckDB and, with `ATLAS_TEST_POSTGRES_URL`, PostgreSQL); frontend `src/test/prompt-library-*.test.*`;
end-to-end `test/pr-validation/test_pr1169_team_prompt_library.sh`.
