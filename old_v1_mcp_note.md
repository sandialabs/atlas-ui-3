## MCP Tool Output Contract

Every MCP tool MUST return a JSON object containing a top-level field named `results`.

The application logic assumes this contract so it can reliably:
1. Add `results` (and certain optional fields) into the model prompt context.
2. Persist file artifacts out-of-band (so large content does not bloat the prompt).
3. Support consistent UI rendering across heterogeneous tool providers (FastMCP, custom servers, etc.).

---
### Required Field
- `results`: The primary, human/model readable result of the tool. This should be concise but complete. Prefer structured JSON arrays/objects over long free-form prose when possible. If there is a natural tabular shape, either return an array of objects or a small markdown table inside `results`.

### Optional Fields (Recognized by Backend)
- `meta_data` (object) – Additional structured facts (metrics, timings, parameters used). These are ALSO added to model context (keep it small & relevant). Note: Previous spelling in code/comments sometimes used `meta-data`; prefer `meta_data` or align with actual implementation.
- `returned_file_names` (array[str]) – Filenames corresponding to any artifact blobs produced. These names WILL be included in the LLM prompt (so choose informative, short names).
- `returned_file_contents` (array[str|object]) – Base64-encoded file payloads (or objects with `{name, b64}` depending on helper). These are stored in session state but NOT injected into the LLM prompt directly. Retrieval/preview happens via separate UI fetches.

If a tool produces no files, simply omit `returned_file_names` / `returned_file_contents`.

### Size / Safety Guidelines
- Keep `results` under a few KB where feasible. Push large data to files instead.
- Do NOT base64 large binaries and also echo summaries in `results`—choose one strategy and reference the file names.
- Avoid secrets or credential material in any field; redaction is the tool author's responsibility.

### Minimal Examples
#### 1. Simple Calculation
```json
{
	"results": {"expression": "234*97", "result": 22698}
}
```

#### 2. With Metadata
```json
{
	"results": {"row_count": 42},
	"meta_data": {"elapsed_ms": 18, "source": "inventory_db"}
}
```

#### 3. With File Artifacts
```json
{
	"results": "Generated embedding vectors (see files).",
	"returned_file_names": ["embeddings_part1.json", "embeddings_part2.json"],
	"returned_file_contents": [
		"eyJ2ZWN0b3JzIjpbMC4xMjMsMC4zNDUuLi5dfQ==",
		"eyJ2ZWN0b3JzIjpbMC4yMjMsMC40NDUuLi5dfQ=="
	],
	"meta_data": {"dimension": 1536, "chunks": 2}
}
```

---
## FastMCP Integration Notes

FastMCP often exposes a `CallToolResult` object. A typical Python `repr` may look like:

```
CallToolResult(
	content=[TextContent(type='text', text='{"operation":"evaluate","expression":"234*97","result":22698}')],
	structured_content={'operation': 'evaluate', 'expression': '234*97', 'result': 22698},
	data={'operation': 'evaluate', 'expression': '234*97', 'result': 22698},
	is_error=False
)
```

Extraction strategy for our contract:
1. Prefer `structured_content` if present (already parsed dict).
2. Fallback: parse the first `TextContent.text` as JSON if possible.
3. Wrap the extracted payload inside `{ "results": <payload> }`.

So the transformed outbound JSON becomes:
```json
{
	"results": {"operation": "evaluate", "expression": "234*97", "result": 22698}
}
```

If FastMCP tool itself already matches the contract (i.e., it explicitly returns `{"results": ...}`) then forward unchanged.

### Edge Cases
- If `is_error` is true: map to `{ "results": {"error": <message>}, "meta_data": {"is_error": true} }` (or follow existing error handling conventions in the backend if different—update this doc if behavior diverges).
- If both `structured_content` AND `data` exist but differ, choose `structured_content` and log a warning.
- Empty outputs: return `{ "results": null }` rather than `{}` for clarity.

---
## Implementation Checklist for a New Tool
1. Produce core payload → assign to `results`.
2. Decide if any supporting metrics belong in `meta_data`.
3. Stream or accumulate large artifacts → put names in `returned_file_names`, base64 content in same-order array `returned_file_contents`.
4. Validate JSON serializability.
5. Keep contract stable; client-side/UI logic depends on the field names.

---
## Common Mistakes (Avoid)
- Misspelling `results` (causes silent drops or missing context).
- Placing gigantic raw text in `results` instead of using file artifacts.
- Embedding binary (non-base64) directly in JSON.
- Returning a plain list at top level instead of an object with `results`.
- Using both `meta-data` and `meta_data` inconsistently. Pick one (current recommendation: `meta_data`).

---
## Quick Reference
| Field | Required | Added to LLM Prompt | Purpose |
|-------|----------|---------------------|---------|
| results | Yes | Yes | Primary structured result |
| meta_data | No | Yes | Supplemental small metadata |
| returned_file_names | No | Yes (names only) | Reference to stored artifacts |
| returned_file_contents | No | No (persisted only) | Base64 content of artifacts |

---
Update this note if backend parsing rules change. Keep it succinct and authoritative—treat this as the single source of truth for tool output formatting.
