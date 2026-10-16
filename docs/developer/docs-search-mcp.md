# Docs Search MCP Example

Last updated: 2026-10-16

`atlas/mcp/docs_search/main.py` is a small, read-only **stdio** MCP server that
lets the LLM search and read this repository's `docs/` folder. It is a
reference for building a retrieval-style tool server with no external
dependencies (standard library plus FastMCP), and is handy in local
development for asking Atlas questions about Atlas itself.

## Tools

| Tool | Purpose |
|------|---------|
| `search_docs(query, limit=8, include_archive=False)` | Rank Markdown pages by keyword overlap with the query. Title, heading, and file-path matches outweigh body matches, and pages covering more distinct query words rank higher. Common stopwords ("how", "do", "the", ...) are ignored, and hyphenated words and filenames are split, so `whitelist` matches `domain-whitelist.md`. Each match returns `path`, `title`, `score`, `matched_terms`, and up to three line-numbered `snippets`. |
| `read_doc(path, start_line=1, max_lines=200)` | Return a page, or a line range of it (max 400 lines per call), with `total_lines` and a `truncated` flag so the model can page through long docs. |
| `list_docs(section=None, include_archive=False)` | List pages with titles, optionally limited to one top-level folder such as `admin` or `developer`. |

A typical turn: the model calls `search_docs`, picks a match, then calls
`read_doc` with `start_line` set a few lines above a snippet's `line`.

`docs/archive/` (shipped or abandoned plans) is skipped unless
`include_archive=True`, matching the AI doc bundle, so stale designs do not
outrank current reference pages.

## Enabling it

The example config is `atlas/config/mcp-example-configs/mcp-docs_search.json`.
Add it from the admin panel's MCP server catalog, or merge it into
`config/mcp.json`:

```json
{
  "docs_search": {
    "command": ["python", "mcp/docs_search/main.py"],
    "cwd": "atlas",
    "groups": ["users"],
    "short_description": "Search and read project documentation",
    "compliance_level": "Public"
  }
}
```

Then reload MCP config (`POST /admin/mcp/reload`) or restart the backend.

## Pointing at a different folder

By default the server reads `<repo>/docs`, resolved from its own file
location, which works from a source checkout. A package install has no repo
docs folder, so without an override every tool returns a "Docs folder not
found" error rather than indexing unrelated files. To use it there, or to
index another Markdown tree, pass `ATLAS_DOCS_ROOT` through the
server's `env` block (stdio servers do not inherit the backend environment;
see [MCP Server Configuration](../admin/mcp-servers.md#stdio-environment-isolation)):

```json
"env": {"ATLAS_DOCS_ROOT": "/srv/atlas/docs"}
```

## Safety

- Read-only: no tool writes to disk.
- Paths are resolved and must stay inside the docs root; `..` traversal and
  symlinks that point outside it are rejected, and only `.md` files are read.
- Tool output uses paths relative to the docs root and never exposes the
  absolute server path.
- Search is a full scan per call. That is fine for a docs folder of a few
  hundred pages; a large corpus should use a real index or the RAG
  integration instead.

## Tests

`atlas/tests/test_mcp_docs_search.py` covers ranking, archive filtering, path
confinement, paging, and an end-to-end run that launches the server over
stdio and searches the real `docs/` folder.
