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
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("Docs Search")

# <repo>/atlas/mcp/docs_search/main.py -> <repo>. Only trusted when it looks
# like a source checkout; in a package install this is site-packages.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DOCS_ROOT = _REPO_ROOT / "docs"
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
TERM_CAP = 5
# Bound per-call work: every term is checked against every line of every doc.
MAX_QUERY_CHARS = 500
MAX_QUERY_TERMS = 16
# Body term frequency saturates BM25-style: BODY_MAX * tf / (tf + BODY_HALF).
# One hit scores 1.0 and no amount of repetition reaches BODY_MAX, so a page
# matching two query words beats a page repeating one.
BODY_MAX = 3.0
BODY_HALF = 2.0

# Hyphens split tokens so "domain-whitelist" matches "whitelist".
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
# CommonMark ATX heading: up to 3 spaces of indent; 4+ is an indented code block.
_HEADING_RE = re.compile(r" {0,3}#{1,6}\s")
_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})")
# Common words dropped from queries so "how do I configure X" ranks on X.
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it me my of on "
    "or the to use what when where which who why with you".split()
)


def _docs_root() -> Optional[Path]:
    """Resolve the docs root, honoring ATLAS_DOCS_ROOT when set.

    Returns None when no usable docs folder exists, including a package
    install where the default would point into site-packages.
    """
    override = os.environ.get("ATLAS_DOCS_ROOT", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    elif (_REPO_ROOT / "pyproject.toml").is_file():
        # Resolve so a symlinked docs/ still passes the containment checks.
        root = DEFAULT_DOCS_ROOT.resolve()
    else:
        return None
    return root if root.is_dir() else None


_ROOT_ERROR = {"results": {"error": "Docs folder not found. Set ATLAS_DOCS_ROOT in the server's env block."}}


def _fold_plural(token: str) -> str:
    """Crude plural folding so "servers" matches "server" (not "class")."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


# Stopwords are compared after folding, like every other token.
_STOPWORDS = frozenset(_fold_plural(w) for w in _STOPWORDS)


def _tokenize(text: str) -> List[str]:
    return [_fold_plural(t) for t in _TOKEN_RE.findall(text.lower())]


def _safe_doc_path(root: Path, path: str) -> Path:
    """Resolve ``path`` under ``root`` and reject anything that escapes it."""
    try:
        # RuntimeError: symlink loop (Python < 3.13); OSError: e.g. name too
        # long. Both messages carry the absolute path, so never echo them.
        candidate = (root / path.lstrip("/")).resolve()
    except (OSError, RuntimeError):
        raise ValueError(f"Cannot resolve document path: '{path}'")
    try:
        candidate.relative_to(root)
    except ValueError:
        raise PermissionError(f"Access denied: '{path}' is outside the docs folder")
    if candidate.suffix.lower() != DOC_SUFFIX:
        raise ValueError(f"Only Markdown ({DOC_SUFFIX}) documents can be read: '{path}'")
    return candidate


def _iter_docs(root: Path, include_archive: bool) -> List[Path]:
    """Markdown files under ``root`` that read_doc would also accept.

    os.walk (no followlinks, errors ignored) skips unreadable directories
    and cannot loop on directory symlinks; file symlinks are kept only when
    their target is a .md file inside the root.
    """
    docs = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        current = Path(dirpath)
        if not include_archive and current == root and ARCHIVE_DIR in dirnames:
            dirnames.remove(ARCHIVE_DIR)
        for name in filenames:
            if not name.lower().endswith(DOC_SUFFIX):
                continue
            path = current / name
            try:
                _safe_doc_path(root, path.relative_to(root).as_posix())
            except (PermissionError, ValueError, OSError):
                continue
            if path.is_file():
                docs.append(path)
    return sorted(docs)


def _heading_flags(lines: List[str]) -> List[bool]:
    """Mark ATX headings ("# ".."###### "), ignoring lines inside fenced
    code blocks so shell comments in examples are not treated as headings."""
    flags = []
    fence = ""  # opening marker of the current fence, "" when outside one
    for line in lines:
        m = _FENCE_RE.match(line)
        if m:
            marker = m.group(1)
            if not fence:
                # A backtick fence's info string cannot contain backticks, so
                # "```js```" is an inline code span, not an opening fence.
                if marker[0] == "`" and "`" in line[m.end():]:
                    flags.append(False)
                    continue
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not line[m.end():].strip():
                fence = ""
            flags.append(False)
            continue
        flags.append(not fence and bool(_HEADING_RE.match(line)))
    return flags


def _doc_title(lines: List[str], fallback: str) -> str:
    """First level-1 heading outside code fences, else ``fallback``."""
    for line, is_heading in zip(lines, _heading_flags(lines)):
        stripped = line.lstrip()
        if is_heading and stripped.startswith("# ") and stripped[2:].strip():
            return stripped[2:].strip()
    return fallback


def _score_doc(rel_path: str, lines: List[str], terms: List[str]) -> Dict[str, Any]:
    """Score a document against query terms and collect matching line numbers.

    Body counts saturate (BODY_MAX) and heading counts are capped
    (TERM_CAP) so a long page that repeats a word does not outrank a short
    page that is about it.
    """
    title = _doc_title(lines, rel_path)
    title_tokens = set(_tokenize(title))
    path_tokens = set(_tokenize(rel_path.removesuffix(DOC_SUFFIX)))
    body_counts = dict.fromkeys(terms, 0)
    heading_counts = dict.fromkeys(terms, 0)
    hit_lines: List[Tuple[int, int]] = []

    for idx, (line, is_heading) in enumerate(zip(lines, _heading_flags(lines))):
        tokens = _tokenize(line)
        if not tokens:
            continue
        line_counts = Counter(tokens)
        distinct = 0
        for term in terms:
            count = line_counts.get(term, 0)
            if count:
                distinct += 1
                body_counts[term] += count
                if is_heading:
                    heading_counts[term] += count
        if distinct:
            hit_lines.append((idx, distinct))

    score = 0.0
    matched_terms = {t for t in terms if body_counts[t]}
    for term in terms:
        tf = body_counts[term]
        score += BODY_MAX * tf / (tf + BODY_HALF)
        score += HEADING_WEIGHT * min(heading_counts[term], TERM_CAP)
        if term in title_tokens:
            score += TITLE_WEIGHT
            matched_terms.add(term)
        if term in path_tokens:
            score += PATH_WEIGHT
            matched_terms.add(term)

    # Favor documents that cover more of the query, not just repeat one word.
    score = round(score * len(matched_terms), 2)
    return {"title": title, "score": score, "matched_terms": sorted(matched_terms), "hit_lines": hit_lines}


def _snippets(lines: List[str], hit_lines: List[Tuple[int, int]]) -> List[Dict[str, Any]]:
    """Pick up to MAX_SNIPPETS_PER_DOC non-overlapping snippets, preferring
    lines that match the most distinct query terms, returned in page order."""
    chosen: List[Tuple[int, int, int]] = []
    for idx, _ in sorted(hit_lines, key=lambda h: (-h[1], h[0])):
        start = max(0, idx - SNIPPET_RADIUS)
        end = min(len(lines) - 1, idx + SNIPPET_RADIUS)
        if any(start <= c_end and end >= c_start for c_start, c_end, _ in chosen):
            continue
        chosen.append((start, end, idx))
        if len(chosen) >= MAX_SNIPPETS_PER_DOC:
            break
    return [
        {"line": idx + 1, "text": "\n".join(lines[start:end + 1]).strip()}
        for start, end, idx in sorted(chosen)
    ]


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
            Only the first 500 characters and 16 distinct non-stopword
            terms are used.
        limit: Maximum number of results to return (1-25, default 8).
        include_archive: Also search docs/archive (historical plans). Default False.

    Returns:
        Dictionary with "results" containing query (as used, truncated to
        500 characters), terms (the words actually searched, after
        stopwords and the term cap), total_matches, and
        matches: a list ordered best-first, each with path (relative to the
        docs folder), title, score, matched_terms, and up to three snippets
        containing a 1-based line number and surrounding text.
    """
    # Map each folded term back to the word the caller typed, for display.
    original: Dict[str, str] = {}
    for word in _TOKEN_RE.findall(query[:MAX_QUERY_CHARS].lower()):
        original.setdefault(_fold_plural(word), word)
    tokens = list(original)
    # Keep stopwords only when the query is nothing but stopwords.
    terms = ([t for t in tokens if t not in _STOPWORDS] or tokens)[:MAX_QUERY_TERMS]
    if not terms:
        return {"results": {"error": "Query must contain at least one letter or digit"}}

    root = _docs_root()
    if root is None:
        return _ROOT_ERROR

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
            "matched_terms": sorted(original[t] for t in scored["matched_terms"]),
            "snippets": _snippets(lines, scored["hit_lines"]),
        })

    matches.sort(key=lambda m: (-m["score"], m["path"]))
    return {
        "results": {
            "query": query[:MAX_QUERY_CHARS],
            "terms": [original[t] for t in terms],
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
    if root is None:
        return _ROOT_ERROR
    try:
        doc_path = _safe_doc_path(root, path)
    except (PermissionError, ValueError) as e:
        return {"results": {"error": str(e)}}
    try:
        exists = doc_path.is_file()
    except OSError:
        exists = False
    if not exists:
        return {"results": {"error": f"Document not found: {path}"}}

    try:
        lines = doc_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        # e.strerror only: str(e) would leak the absolute server path.
        return {"results": {"error": f"Error reading document {path}: {e.strerror}"}}
    total = len(lines)
    start = max(1, int(start_line))
    if total and start > total:
        return {"results": {"error": f"start_line {start} is past the end of {path} ({total} lines)"}}
    count = max(1, min(int(max_lines), MAX_READ_LINES))
    end = min(total, start + count - 1)
    return {
        "results": {
            "path": doc_path.relative_to(root).as_posix(),
            "title": _doc_title(lines, doc_path.relative_to(root).as_posix()),
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
        Dictionary with "results" containing count and documents
        (each with path and title).
    """
    root = _docs_root()
    if root is None:
        return _ROOT_ERROR

    section_name = (section or "").strip("/")
    if section_name == ARCHIVE_DIR:
        include_archive = True
    documents = []
    for path in _iter_docs(root, include_archive):
        rel = path.relative_to(root)
        if section_name and (len(rel.parts) < 2 or rel.parts[0] != section_name):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        # Same title rule as search_docs and read_doc, so tools agree.
        documents.append({"path": rel.as_posix(), "title": _doc_title(lines, rel.as_posix())})

    return {"results": {"count": len(documents), "documents": documents}}


if __name__ == "__main__":
    mcp.run(show_banner=False)
