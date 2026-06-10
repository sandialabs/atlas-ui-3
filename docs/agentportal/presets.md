# Preset library

Users commonly rerun the same launch configuration — the same `claude`
invocation against the same repo, a different agent against a different
workspace, and so on. The preset library stores those setups server-side
so they survive browser cache clears and can be edited centrally.

## Storage

- Path: `<APP_CONFIG_DIR>/agent_portal_presets.json`, default
  `config/agent_portal_presets.json` at the repo root.
- Shape: `{"schema_version": 1, "presets": [...]}`.
- Writes are atomic (temp file + `os.replace`) and serialized by a
  `fcntl.flock` on a sibling `.lock` file so two browser tabs cannot
  corrupt the file under a concurrent save.
- Every row carries `user_email`; the store filters by owner on every
  read and write, so one user cannot see, edit, or delete another
  user's presets. Unlike the per-process `/processes/{id}` endpoints —
  which still defer ownership checks to graduation — presets enforce
  ownership at the storage layer from day one.

## HTTP API

All endpoints live under the existing agent-portal router, are gated
by `FEATURE_AGENT_PORTAL_ENABLED`, and require the same authenticated
user as the rest of the portal.

| Method | Path | Body | Result |
|---|---|---|---|
| `GET`    | `/api/agent-portal/presets`             |  — | `{"presets": [...]}` |
| `POST`   | `/api/agent-portal/presets`             | `PresetCreateRequest` | 201 + preset |
| `GET`    | `/api/agent-portal/presets/{id}`        |  — | preset or 404 |
| `PATCH`  | `/api/agent-portal/presets/{id}`        | `PresetUpdateRequest` (partial) | updated preset or 404 |
| `DELETE` | `/api/agent-portal/presets/{id}`        |  — | 204 or 404 |

Field set mirrors `LaunchRequest` plus `name` (required) and
`description` (free-form). `id`, `user_email`, and `created_at` are
server-assigned and immutable through the update endpoint.

## Frontend behavior

- On first mount, the portal fetches the user's presets. If the server
  is empty and there are legacy `localStorage` entries (pre-graduation
  `atlas.agentPortal.launchConfigs.v1`), it posts them one at a time to
  the server and clears the key once all migrated cleanly.
- Clicking a preset loads it into the launch form and marks it as the
  "loaded" preset. An **Update** button then appears next to **Save
  as…**, which PATCHes the form back to that preset. Editing the form
  never implicitly mutates a preset.
- `Save as…` prompts for a name, then an optional description, and
  POSTs a new preset.
- The `X` button deletes. For legacy `cfg_*` ids still living in
  localStorage, delete only rewrites the local cache; `pst_*` ids go to
  the server.

## Future work

- Import / export for moving presets between machines. Defer until the
  feature is closer to graduation; the JSON file is already trivially
  copyable.
- Built-in read-only examples (e.g. "Claude in current repo"). Kept out
  of v1 to avoid opinions; users can build their own.
