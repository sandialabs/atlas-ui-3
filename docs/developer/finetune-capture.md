# Fine-tune Capture

Last updated: 2026-06-25

Opt-in capture of chat traffic for fine-tuning a small, customized model. When a
user opts in **and** an administrator has enabled the feature, Atlas records the
full LLM input/output for each tool-capable turn — prompts, completions, tool
calls, tool results, and the available-tool list — to a local JSONL store that
can be exported as SFT examples or DPO preference pairs.

For the design rationale and history, see the
[design note](design-notes/finetune-capture-2026-06-25.md).

## Two-flag consent

Capture happens only when **both** are true at the moment a turn runs:

| Layer | Control | Default |
|---|---|---|
| System | `FEATURE_FINETUNE_CAPTURE_ENABLED` (env) | `false` |
| User | Per-user consent (Settings -> "Help improve Atlas") | off |

Opting in is rejected while the system flag is off, so a user can never be
recorded against a disabled system.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `FEATURE_FINETUNE_CAPTURE_ENABLED` | `false` | System gate for the whole feature. |
| `RUNTIME_CAPTURE_DIR` | `runtime/finetune_capture` | Where consent and captured turns are stored. |
| `CAPTURE_USER_SALT` | built-in default | Salt for pseudonymizing user identifiers. **Set this** for real pseudonymity across deployments. |

## Storage layout

```
<RUNTIME_CAPTURE_DIR>/
├── consent/<user_hash>.json              one consent record per user
└── data/<YYYY-MM-DD>/<user_hash>.jsonl   one captured turn per line
```

`<user_hash>` is a salted SHA-256 of the user's email — raw emails never appear
in filenames or records.

Each record is either:

- `kind: "turn"` — a single trajectory (SFT material; no `rejected` side), or
- `kind: "pair"` — a `(rejected, chosen)` preference pair from a rollback
  correction (DPO material).

Records also retain the raw per-LLM-call `transcript` for full fidelity and pin
the available-tool schemas at capture time (so old examples don't teach tools
that no longer exist).

## The three signals

1. **Rollback + force-tool (highest signal).** On an assistant turn, the user
   clicks "Correct this turn", picks the tool the model *should* have called, and
   re-runs. Atlas narrows the tool list to that one tool (the provider-safe way
   to force a choice — forced `tool_choice="required"` was removed in PR #664),
   re-runs the turn, and saves both versions as a DPO pair.
2. **End-of-conversation rating** (weak global signal) — reuses the existing
   feedback widget.
3. **Implicit** — never written as success. Silence is treated as `unknown` at
   low confidence so SFT pipelines can filter it out.

## HTTP API

User endpoints (any authenticated user):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/capture/consent` | Current opt-in + system flag state |
| `POST` | `/api/capture/consent` | Set/revoke opt-in (`{"enabled": bool}`) |
| `DELETE` | `/api/capture/me` | Delete all of the caller's captured data |

Admin endpoints (member of `admin_group`):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/admin/capture/stats` | Counts, opt-in rate, storage size |
| `GET` | `/api/admin/capture/export` | Streamed JSONL export (`?start_date=&end_date=`) |

The rollback correction itself is sent over the chat WebSocket (a normal chat
message carrying `rewind_to_user_index`, a one-element `selected_tools`, and a
`capture_correction` payload), not via REST.

## Exporting training data

```bash
# Preference pairs for DPO (drops records with no rejected side)
atlas-finetune-export --format dpo -o pairs.jsonl

# SFT examples from the chosen side only
atlas-finetune-export --format sft --start-date 2026-06-01 -o sft.jsonl

# The raw stored records, unchanged
atlas-finetune-export --format raw
```

The exporter reads `RUNTIME_CAPTURE_DIR` (or `--capture-dir`). The store format
is stable, so downstream training pipelines can evolve independently.

## Privacy notes

Full capture means full PII capture — tool results can include database rows,
file contents, or secrets. Capture is off by default at both levels, every
record is pseudonymized, and users can self-delete at any time. PII scrubbing is
intentionally **not** applied (it degrades training data); deployments that need
it should treat the capture directory as sensitive and restrict access
accordingly.
