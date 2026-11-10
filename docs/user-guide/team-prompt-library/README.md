# Team Prompt Library (issue #1169)

Last updated: 2026-11-10

The team prompt library lets you keep versioned system prompts and share them
with the Atlas groups you belong to. It sits next to your private
[custom prompts](../custom-prompts/README.md) in **Tools and Settings ->
Prompts**, and its published entries appear under **Team Library** in the
prompt picker above the chat box.

The library is available whenever custom prompts are (`FEATURE_CUSTOM_PROMPTS_ENABLED`
plus chat history). Private custom prompts keep working exactly as before.

![Team Library panel](screenshots/1169-01-library-panel.png)

## Browse and search

Filter the list with **All**, **Mine** (entries you own), **Shared with me**
(entries someone shared with you by name) and **Team** (entries shared with a
group you are in), and search by title or description. Each row shows the
owner, how it is shared, the latest published version, and your access level.
You only ever see entries you are allowed to use.

## Create, edit and publish

**New library prompt** creates a **private** entry: nobody else can see it
until you share it. To bring over an existing private custom prompt, use the
book icon next to it under Custom Prompts (**Save to team library**); it is
copied as a new private entry.

Editing changes the **draft** only. If someone else saved the draft after you
opened it, your save or publish is refused with a message asking you to reload
-- nobody can publish text they did not see. Use **Preview** to read the draft, and
**Test in chat** to try it on your next messages without publishing (the test
selection is not saved and does not affect anyone else). **Publish version**
saves the draft as the next numbered version with an optional change
description. Published versions never change.

## Share

Owners and people with manage permission open the share editor:

![Sharing editor](screenshots/1169-02-sharing.png)

- **Groups** let their members view and use published versions. You can only
  share with groups you are a member of. Group membership never allows editing
  or re-sharing. The everyone-group `users` cannot be shared with: the library
  has no organization-wide catalog, and the server refuses such a grant.
- **Named people** can be given **Use**, **Edit & publish**, or **Manage
  sharing**. Only the owner can grant or remove manage permission.

Access is the union of everything that applies to you: leaving one group does
not remove access you still have through another group or a direct grant.
Membership is checked on every request, so changes take effect immediately.

## Versions and chat

Pick an entry in the prompt picker (or **Use** in the library) to follow its
latest version, or open **Version history** to choose a specific version.

![Version history](screenshots/1169-03-versions.png)

The conversation records the exact version it ran with:

- A conversation keeps that version even after a newer one is published.
  Choose the newer version explicitly to switch.
- Reopening the conversation from the sidebar re-selects that version, even
  when it also restores the conversation's workspace.
- If the entry is unshared, archived, or your access is removed, Atlas refuses
  to run it and asks you to choose another prompt. It never silently switches
  to a different prompt. The conversation's history is kept either way.
- A client that cannot choose a prompt per message (the `atlas-chat` CLI,
  follow-up and API runs, older browser versions) is told how to continue: the
  conversation's owner clears its team prompt with
  `DELETE /api/prompts/pins/{conversation_id}`, and the next run continues
  with the default prompt. Clearing is recorded in the prompt's audit trail.

**Fork** copies a published version into a new private entry you own; changes
to either entry do not affect the other. Owners can **archive** an entry: it
disappears from the library and can no longer be used, while conversations that
used it keep their history.

Saved [workspaces](../workspaces/README.md) can include a library selection.
If you lose access to it, the picker shows **Unavailable team prompt** and the
send is refused until you choose another prompt.

## API

All endpoints take your identity from authentication, never from the request
body. An entry you cannot see answers `404 Prompt not found`, exactly like an id
that does not exist.

| Method | Path | Needs |
|--------|------|-------|
| GET | `/api/prompts?scope=all\|mine\|shared\|team&q=&limit=&offset=` | -- (paged: `{prompts, total, limit, offset}`; `limit` 1-200, default 50) |
| POST | `/api/prompts` | -- (creates a private entry) |
| GET | `/api/prompts/groups` | -- (groups you may share with) |
| GET | `/api/prompts/{id}` | use (draft only for edit) |
| PUT | `/api/prompts/{id}` | edit (body names `expected_draft_revision`) |
| DELETE | `/api/prompts/{id}` | owner (archives) |
| POST | `/api/prompts/{id}/publish` | edit (body names `expected_draft_revision`) |
| GET | `/api/prompts/{id}/versions[/{version_id}]` | use |
| GET/PUT | `/api/prompts/{id}/sharing` | manage (GET returns `revision`; PUT names `expected_revision`) |
| POST | `/api/prompts/{id}/fork` | use |
| GET | `/api/prompts/{id}/export` | use |
| GET | `/api/prompts/{id}/audit` | manage |
| GET | `/api/prompts/{id}/resolve?version_id=` | use (read-only advisory check) |
| DELETE | `/api/prompts/pins/{conversation_id}` | conversation owner |

Search (`q`) matches title or description literally; `%` and `_` are not
wildcards. `409` means the draft or sharing changed since you loaded it (reload
and retry); `503` means the library's storage is unavailable.

A chat frame names a library entry with
`"library_prompt": {"prompt_id": "...", "version_id": "..." | null}` (`null` =
latest; `"library_prompt": null` = no library prompt). The server resolves and
authorizes it on every turn; prompt text never comes from the client. See the
[design note](../../developer/design-notes/team-prompt-library-2026-11-09.md)
for the full rules.
