#!/usr/bin/env python3
"""
Docs Search MCP Server using FastMCP.

Keyword search and retrieval over the repository's Markdown documentation
folder (``docs/``). Intended as a local-development example of a read-only
stdio MCP server that grounds answers in project docs: the LLM calls
``search_docs`` to find relevant pages, then ``read_doc`` to pull the exact
section it needs.

The docs root defaults to ``<repo>/docs`` resolved from this file's location,
so it works from a source checkout. Set ``ATLAS_DOCS_ROOT`` (via the server's
``env`` block in mcp.json) to point at a different folder, for example when
Atlas is installed as a package and the repository docs live elsewhere.

All paths are confined to the docs root; ``..`` traversal and symlinks that
escape it are rejected. Only ``.md`` files are indexed or read.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("Docs Search")

DEFAULT_DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"
DOC_SUFFIX = ".md"
# docs/archive holds shipped/abandoned plans and is excluded from the AI doc
# bundle; skip it by default so stale designs do not outrank current docs.
ARCHIVE_DIR = "archive"
MAX_RESULTS = 25
MAX_READ_LINES = 400
SNIPPET_RADIUS = 1
MAX_SNIPPETS_PER_DOC = 3
TITLE_WEIGHT = 5
HEADING_WEIGHT = 3
PATH_WEIGHT = 2

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")
# Common words dropped from queries so "how do I configure X" ranks on X.
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it me my of on "
    "or the to use what when where which who why with you".split()
)


def _docs_root() -> Path:
    """Resolve the docs root, honoring ATLAS_DOCS_ROOT when set."""
    override = os.environ.get("ATLAS_DOCS_ROOT", "").strip()
    return Path(override).expanduser().resolve() if override else DEFAULT_DOCS_ROOT


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def _safe_doc_path(root: Path, path: str) -> Path:
    """Resolve ``path`` under ``root`` and reject anything that escapes it."""
    candidate = (root / path.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise PermissionError(f"Access denied: '{path}' is outside the docs folder")
    if candidate.suffix.lower() != DOC_SUFFIX:
        raise ValueError(f"Only Markdown ({DOC_SUFFIX}) documents can be read: '{path}'")
    return candidate


def _iter_docs(root: Path, include_archive: bool) -> List[Path]:
    docs = []
    for path in sorted(root.rglob(f"*{DOC_SUFFIX}")):
        rel = path.relative_to(root)
        if not include_archive and rel.parts and rel.parts[0] == ARCHIVE_DIR:
            continue
        # A symlink inside docs/ may point outside it; never follow those.
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        if path.is_file():
            docs.append(path)
    return docs


def _doc_title(lines: List[str], fallback: str) -> str:
    for line in lines:
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _score_doc(rel_path: str, lines: List[str], terms: List[str]) -> Dict[str, Any]:
    """Score a document against query terms and collect matching line numbers."""
    title = _doc_title(lines, rel_path)
    title_tokens = set(_tokenize(title))
    path_tokens = set(_tokenize(rel_path.replace("/", " ")))
    score = 0
    matched_terms = set()
    hit_lines: List[int] = []

    for idx, line in enumerate(lines):
        tokens = _tokenize(line)
        if not tokens:
            continue
        line_hits = 0
        for term in terms:
            count = tokens.count(term)
            if count:
                matched_terms.add(term)
                line_hits += count
                if line.lstrip().startswith("#"):
                    score += HEADING_WEIGHT * count
        if line_hits:
            score += line_hits
            hit_lines.append(idx)

    for term in terms:
        if term in title_tokens:
            score += TITLE_WEIGHT
            matched_terms.add(term)
        if term in path_tokens:
            score += PATH_WEIGHT
            matched_terms.add(term)

    # Favor documents that cover more of the query, not just repeat one word.
    score *= len(matched_terms)
    return {"title": title, "score": score, "matched_terms": sorted(matched_terms), "hit_lines": hit_lines}


def _snippets(lines: List[str], hit_lines: List[int]) -> List[Dict[str, Any]]:
    snippets: List[Dict[str, Any]] = []
    last_end = -1
    for idx in hit_lines:
        start = max(0, idx - SNIPPET_RADIUS)
        if start <= last_end:
            continue
        end = min(len(lines) - 1, idx + SNIPPET_RADIUS)
        snippets.append({
            "line": idx + 1,
            "text": "\n".join(lines[start:end + 1]).strip(),
        })
        last_end = end
        if len(snippets) >= MAX_SNIPPETS_PER_DOC:
            break
    return snippets


@mcp.tool
def search_docs(query: str, limit: int = 8, include_archive: bool = False) -> Dict[str, Any]:
    """
    Search the project's Markdown documentation for pages relevant to a query.

    Ranks every document under the docs folder by keyword overlap with the
    query, weighting matches in the page title, section headings, and file
    path above matches in body text. Pages that match more distinct query
    words rank higher than pages that repeat a single word.

    Use this first to find where a topic is documented, then call read_doc
    with a returned path (and optionally a line number) to read the details.

    Args:
        query: Free-text search terms, e.g. "mcp server authentication jwt".
        limit: Maximum number of results to return (1-25, default 8).
        include_archive: Also search docs/archive (historical plans). Default False.

    Returns:
        Dictionary with "results": a list of matches, each with path (relative
        to the docs folder), title, score, matched_terms, and up to three
        snippets containing a 1-based line number and surrounding text.
    """
    tokens = list(dict.fromkeys(_tokenize(query)))
    # Keep stopwords only when the query is nothing but stopwords.
    terms = [t for t in tokens if t not in _STOPWORDS] or tokens
    if not terms:
        return {"results": {"error": "Query must contain at least one letter or digit"}}

    root = _docs_root()
    if not root.is_dir():
        return {"results": {"error": f"Docs folder not found: {root}. Set ATLAS_DOCS_ROOT."}}

    limit = max(1, min(int(limit), MAX_RESULTS))
    matches = []
    for path in _iter_docs(root, include_archive):
        rel_path = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        scored = _score_doc(rel_path, lines, terms)
        if scored["score"] <= 0:
            continue
        matches.append({
            "path": rel_path,
            "title": scored["title"],
            "score": scored["score"],
            "matched_terms": scored["matched_terms"],
            "snippets": _snippets(lines, scored["hit_lines"]),
        })

    matches.sort(key=lambda m: (-m["score"], m["path"]))
    return {
        "results": {
            "query": query,
            "total_matches": len(matches),
            "matches": matches[:limit],
        }
    }


@mcp.tool
def read_doc(path: str, start_line: int = 1, max_lines: int = 200) -> Dict[str, Any]:
    """
    Read a Markdown document from the docs folder, optionally a line range.

    Use a path returned by search_docs or list_docs. For long documents, pass
    start_line (e.g. a snippet's line number minus a few lines) to read just
    the relevant section instead of the whole page.

    Args:
        path: Document path relative to the docs folder, e.g. "admin/mcp-servers.md".
        start_line: 1-based line to start reading from (default 1).
        max_lines: Maximum number of lines to return (1-400, default 200).

    Returns:
        Dictionary with "results" containing path, title, start_line,
        end_line, total_lines, truncated (whether more lines follow), and
        content. Returns an error for paths outside the docs folder or
        non-Markdown files.
    """
    root = _docs_root()
    try:
        doc_path = _safe_doc_path(root, path)
    except (PermissionError, ValueError) as e:
        return {"results": {"error": str(e)}}
    if not doc_path.is_file():
        return {"results": {"error": f"Document not found: {path}"}}

    lines = doc_path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = len(lines)
    start = max(1, int(start_line))
    count = max(1, min(int(max_lines), MAX_READ_LINES))
    end = min(total, start + count - 1)
    return {
        "results": {
            "path": doc_path.relative_to(root).as_posix(),
            "title": _doc_title(lines, doc_path.name),
            "start_line": start,
            "end_line": end,
            "total_lines": total,
            "truncated": end < total,
            "content": "\n".join(lines[start - 1:end]),
        }
    }


@mcp.tool
def list_docs(section: Optional[str] = None, include_archive: bool = False) -> Dict[str, Any]:
    """
    List available documentation pages with their titles.

    Args:
        section: Optional top-level folder to list, e.g. "admin", "developer",
            or "user-guide". Lists every section when omitted.
        include_archive: Include docs/archive pages. Default False.

    Returns:
        Dictionary with "results" containing docs_root, count, and documents
        (each with path and title).
    """
    root = _docs_root()
    if not root.is_dir():
        return {"results": {"error": f"Docs folder not found: {root}. Set ATLAS_DOCS_ROOT."}}

    section_name = (section or "").strip("/")
    if section_name == ARCHIVE_DIR:
        include_archive = True
    documents = []
    for path in _iter_docs(root, include_archive):
        rel = path.relative_to(root)
        if section_name and (len(rel.parts) < 2 or rel.parts[0] != section_name):
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                head = [next(f, "") for _ in range(20)]
        except OSError:
            continue
        documents.append({"path": rel.as_posix(), "title": _doc_title(head, rel.name)})

    return {"results": {"docs_root": str(root), "count": len(documents), "documents": documents}}


if __name__ == "__main__":
    mcp.run(show_banner=False)
