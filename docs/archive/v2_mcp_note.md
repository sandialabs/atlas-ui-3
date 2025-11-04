## MCP v2 Tool Contract (Artifacts + Display) with Dashboard Option

This v2 spec extends v1 with typed artifacts, a display block for canvas behavior, and a future-proof dashboard option via iframe. It remains backward compatible with v1 (`returned_file_names` + `returned_file_contents`).

---
### 1) Required and Optional Fields

- required:
  - `results` (any JSON): concise primary result for model and user. Prefer structured JSON over long prose.

- optional (recommended):
  - `meta_data` (object): small, structured facts like metrics, parameters, provenance. Kept small (<4KB).
  - `artifacts` (array of objects): preferred for v2. Each artifact:
    - `name` (string): file name (e.g., `report.html`, `plot.png`).
    - `b64` (string): base64 content.
    - `mime` (string): MIME type (e.g., `text/html`, `image/png`, `application/pdf`).
    - `size` (int, optional): bytes.
    - `description` (string, optional): short human label.
    - `viewer` (string, optional): hint like `image|pdf|html|code|auto`.
  - `display` (object): UI hints for the canvas when artifacts are returned.
    - `open_canvas` (bool, default false): whether to open the canvas.
    - `primary_file` (string): which artifact `name` to show first.
    - `mode` (string): `replace|append|split` (UI may treat as hint).
    - `viewer_hint` (string): `auto|image|pdf|html|code`.
  - backward-compat arrays:
    - NOTE: these are being phased out. Do not add to new code.
    - `returned_file_names` (array[str]): legacy names; still recognized.
    - `returned_file_contents` (array[str]): legacy base64; same order as names. Not injected into prompts.

Notes:
- Prefer `artifacts` over legacy arrays. If both are present, `artifacts` wins.
- The backend persists artifacts to storage and only injects names/hints into the model context.

---
### 2) Canvas Behavior (Frontend)

- The UI determines if a file is “showable” by MIME or extension and opens a viewer (image, pdf, html via sandboxed render, or text/markdown/code).
- When multiple artifacts are present:
  - If `display.primary_file` is valid, that artifact shows first; else the first showable artifact is chosen.
  - Remaining artifacts are available via navigation.
- `display.mode` is an intent; the UI may choose the closest layout depending on current capabilities.

Showable defaults (can evolve):
- Images: `image/*` (png, jpg/jpeg, gif, webp, svg)
- PDF: `application/pdf`
- HTML: `text/html` (rendered in a sandboxed container)
- Text/Code: `text/*`, `application/json`, etc. (markdown/code rendering)

---
### 3) Future-proof: Persistent Dashboards via Iframe (Optional)

Some tools may wish to open a persistent, live dashboard. v2 reserves an optional `canvas` envelope without requiring it today:

```
"canvas": {
  "open": true,
  "id": "my-dashboard-123",         // Unique per session/view
  "persistence": "session",          // session|ephemeral|pinned
  "type": "iframe",                  // reserved; future UI support
  "url": "https://tool-host/view",   // iframe src
  "token": "<capability-token>",     // optional short-lived token
  "title": "My Metrics",
  "mode": "replace|append|split"
}
```

Behavioral expectations:
- The UI may pin a tab keyed by `id` and keep it open across tool responses until closed.
- When supported, direct interactivity happens inside the iframe, not via chat; actions are logged server-side with user identity.
- Security: sandbox iframes, allowlist origins, use short-lived capability tokens, and avoid ambient cookies.

This is an option, not a requirement in v2. Inline HTML remains sanitized and non-executable by default (no scripts).

---
### 4) File Inputs and Username Injection (Invocation Contract)

When the model calls a tool, the backend will normalize/inject certain arguments before the call:

- filename(s) to downloadable URLs:
  - If a tool argument named `filename` (string) or `file_names` (array of strings) is present, and those files exist in the current session context, the backend rewrites them to downloadable URLs the tool can fetch directly.
  - Recommended URL shape (planned): `/api/files/download/{file_key}?token=<capability>` so tools can GET without cookies. A lightweight token check in the files API authorizes the download. Until the capability path exists, tools may need to call the JSON endpoint and decode base64.

- username injection (optional):
  - If a tool defines a `username` parameter in its schema, the backend will overwrite any `username` argument with the authenticated user's email/username (`parsed_args['username'] = session.user_email`). Tools should trust this injected value, not any model-provided identity.
  - Tools that do not define a `username` parameter will not receive username injection - this is completely optional.

Tool authors should design tools to accept:
- `filename: string` or `file_names: string[]` as HTTP(S) URLs they will download.
- `username: string` (optional) for authorization decisions and auditing - only if the tool needs user context.

---
### 5) Size and Safety Guidelines

- Keep `results` small; put large payloads into `artifacts`.
- Avoid embedding secrets in any field. Redaction is the tool’s responsibility.
- For HTML artifacts: by default, the UI sanitizes HTML and does not execute scripts. Prefer the future iframe dashboard for interactive UIs.

---
### 6) Examples

1) Simple result only
```json
{
  "results": {"expression": "234*97", "result": 22698}
}
```

2) Artifacts with display hints
```json
{
  "results": {"summary": "Report generated"},
  "meta_data": {"rows": 42, "elapsed_ms": 120},
  "artifacts": [
    {"name": "report.html", "b64": "<base64>", "mime": "text/html", "size": 12345},
    {"name": "chart.png", "b64": "<base64>", "mime": "image/png", "size": 34567}
  ],
  "display": {"open_canvas": true, "primary_file": "report.html", "mode": "replace", "viewer_hint": "html"}
}
```

3) Legacy arrays (still accepted)
```json
{
  "results": "Generated embeddings (see files)",
  "returned_file_names": ["vec1.json", "vec2.json"],
  "returned_file_contents": ["<b64>", "<b64>"],
  "meta_data": {"dimension": 1536, "chunks": 2}
}
```

4) Dashboard (optional; future UI support)
```json
{
  "results": {"status": "opened"},
  "canvas": {
    "open": true,
    "id": "monitor-42",
    "persistence": "session",
    "type": "iframe",
    "url": "https://mcp.example.com/dash?session=abc",
    "token": "<capability-token>",
    "title": "Live Monitor"
  }
}
```

---
### 7) Backend Responsibilities (implemented/planned)

- Normalize v1 and v2 outputs:
  - Prefer `artifacts`; otherwise map legacy `returned_file_names/returned_file_contents`.
  - Persist artifacts to storage; emit `intermediate_update: files_update` with categorized, canvas-displayable files and optional `display.primary_file`.

- Before tool execution (`ChatService._handle_tools_with_updates`):
  - Overwrite `username` from authenticated session (only if the tool schema defines a `username` parameter).
  - Rewrite `filename`/`file_names` to downloadable URLs for files in session context.

- Canvas content:
  - Auto-open the canvas when showable files exist.
  - Prefer the specified `display.primary_file` if present.

- Security and auditing:
  - Do not trust LLM-provided identity; always use injected username.
  - For future dashboards, issue short-lived capability tokens and log actions.

---
### 8) Known UI Notes / Bugs To Align

- Canvas fetch alignment:
  - Current UI fetch path uses `/api/files/download/{s3_key}` but backend exposes `GET /api/files/{file_key}` returning JSON with base64. This must be aligned (either add the download route or adapt the UI to read JSON and construct a Blob).

- Inline HTML interactivity:
  - DOMPurify sanitization prevents scripts/onclick. If interactive dashboards are desired, use the iframe-based dashboard option.

---
### 9) Migration Guidance

- Existing tools that only return `results` continue to work.
- Tools returning `returned_file_names/returned_file_contents` continue to work; consider migrating to `artifacts` with `mime` for better viewer selection.
- Add optional `display` to guide which artifact is shown first.
- For tools that need interactive UIs, plan toward the iframe dashboard option.

---
Treat this document as the source of truth for v2. Keep it concise and update if backend parsing or UI rendering rules change.
