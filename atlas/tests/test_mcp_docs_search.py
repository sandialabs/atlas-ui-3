"""Tests for the docs_search stdio MCP example server.

Tool functions are exercised against a temporary docs tree (via
ATLAS_DOCS_ROOT) with a stubbed server factory, and one end-to-end test
launches the real server over stdio to confirm tool discovery and a search
against the repository's own docs/ folder.
"""

import importlib
import json
import os
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_SCRIPT = REPO_ROOT / "atlas" / "mcp" / "docs_search" / "main.py"


class _DummyMCP:
    def tool(self, func=None):
        if func is None:
            return lambda wrapped: wrapped
        return func


def _load_module(monkeypatch):
    fake_factory = types.ModuleType("atlas.mcp_shared.server_factory")
    fake_factory.create_stdio_server = lambda name: _DummyMCP()
    monkeypatch.setitem(sys.modules, "atlas.mcp_shared.server_factory", fake_factory)
    sys.modules.pop("atlas.mcp.docs_search.main", None)
    return importlib.import_module("atlas.mcp.docs_search.main")


@pytest.fixture
def docs(monkeypatch, tmp_path):
    root = tmp_path / "docs"
    (root / "admin").mkdir(parents=True)
    (root / "developer").mkdir()
    (root / "archive").mkdir()
    (root / "README.md").write_text("# Docs Index\n\nStart here.\n", encoding="utf-8")
    (root / "admin" / "mcp-servers.md").write_text(
        "# MCP Servers\n\nConfigure servers in mcp.json.\n\n"
        "## Authentication\n\nUse a jwt or api_key for authentication.\n",
        encoding="utf-8",
    )
    (root / "developer" / "sampling.md").write_text(
        "# LLM Sampling\n\nTools can call back into the LLM. MCP is mentioned once.\n",
        encoding="utf-8",
    )
    (root / "archive" / "old-auth-plan.md").write_text(
        "# Old Authentication Plan\n\nauthentication authentication authentication\n",
        encoding="utf-8",
    )
    (root / "admin" / "notes.txt").write_text("authentication\n", encoding="utf-8")
    (tmp_path / "secret.md").write_text("# Secret\n", encoding="utf-8")
    monkeypatch.setenv("ATLAS_DOCS_ROOT", str(root))
    return root


# --- search_docs -----------------------------------------------------------

def test_search_ranks_title_and_heading_matches_first(monkeypatch, docs):
    ds = _load_module(monkeypatch)
    res = ds.search_docs("mcp authentication")["results"]

    paths = [m["path"] for m in res["matches"]]
    assert paths[0] == "admin/mcp-servers.md"
    assert "developer/sampling.md" in paths
    top = res["matches"][0]
    assert top["title"] == "MCP Servers"
    assert top["matched_terms"] == ["authentication", "mcp"]
    assert top["snippets"] and all(s["line"] >= 1 for s in top["snippets"])


def test_search_excludes_archive_and_non_markdown_by_default(monkeypatch, docs):
    ds = _load_module(monkeypatch)
    paths = [m["path"] for m in ds.search_docs("authentication")["results"]["matches"]]
    assert "archive/old-auth-plan.md" not in paths
    assert "admin/notes.txt" not in paths

    with_archive = ds.search_docs("authentication", include_archive=True)["results"]
    assert "archive/old-auth-plan.md" in [m["path"] for m in with_archive["matches"]]


def test_search_limit_and_empty_query(monkeypatch, docs):
    ds = _load_module(monkeypatch)
    assert len(ds.search_docs("mcp", limit=1)["results"]["matches"]) == 1
    assert "error" in ds.search_docs("  !!  ")["results"]
    assert ds.search_docs("nonexistentterm")["results"]["matches"] == []


def test_search_ignores_stopwords(monkeypatch, docs):
    ds = _load_module(monkeypatch)
    res = ds.search_docs("how do I use authentication")["results"]
    assert res["matches"][0]["matched_terms"] == ["authentication"]
    # A query of only stopwords still searches rather than erroring.
    assert "error" not in ds.search_docs("how to")["results"]


def test_search_splits_hyphenated_words_and_paths(monkeypatch, docs):
    (docs / "admin" / "domain-whitelist.md").write_text("# Allowed Hosts\n\nUse a domain-whitelist.\n", encoding="utf-8")
    ds = _load_module(monkeypatch)
    top = ds.search_docs("whitelist")["results"]["matches"][0]
    assert top["path"] == "admin/domain-whitelist.md"
    # The hyphenated filename now contributes "servers" as a path token.
    paths = [m["path"] for m in ds.search_docs("servers")["results"]["matches"]]
    assert paths[0] == "admin/mcp-servers.md"


def test_search_term_cap_and_coverage(monkeypatch, docs):
    # Repeating one word must not beat a page covering both terms.
    (docs / "developer" / "spam.md").write_text(
        "# Notes\n\n" + "quota " * 50 + "\n", encoding="utf-8"
    )
    (docs / "developer" / "focused.md").write_text(
        "# Notes\n\nquota limits apply.\n", encoding="utf-8"
    )
    ds = _load_module(monkeypatch)
    matches = ds.search_docs("quota limits")["results"]["matches"]
    assert [m["path"] for m in matches] == ["developer/focused.md", "developer/spam.md"]
    # Saturating body score: 50 repeats stay below BODY_MAX for one term.
    assert matches[1]["score"] < ds.BODY_MAX


def test_fenced_code_comments_are_not_headings(monkeypatch, docs):
    (docs / "developer" / "fenced.md").write_text(
        "Intro text.\n\n```bash\n# install widget\n```\n\n# Real Title\n",
        encoding="utf-8",
    )
    ds = _load_module(monkeypatch)
    res = ds.read_doc("developer/fenced.md")["results"]
    assert res["title"] == "Real Title"
    match = ds.search_docs("widget")["results"]["matches"][0]
    assert match["score"] == 1  # body hit only, no heading boost


def test_fence_rules_and_indented_hashes(monkeypatch, docs):
    # A ~~~ line inside a ``` fence does not close it; a 4-space "# " is code.
    (docs / "developer" / "fences.md").write_text(
        "````md\n~~~\n# not a title\n````\n\n    # indented code\n\n# Real\n",
        encoding="utf-8",
    )
    ds = _load_module(monkeypatch)
    assert ds.read_doc("developer/fences.md")["results"]["title"] == "Real"
    flags = ds._heading_flags(
        (docs / "developer" / "fences.md").read_text(encoding="utf-8").splitlines()
    )
    assert [i for i, f in enumerate(flags) if f] == [7]


def test_inline_triple_backticks_and_empty_heading(monkeypatch, docs):
    (docs / "developer" / "inline.md").write_text(
        "#\n```js``` is inline\n\n# Inline Title\n", encoding="utf-8"
    )
    (docs / "developer" / "blank.md").write_text("# \nbody\n", encoding="utf-8")
    ds = _load_module(monkeypatch)
    assert ds.read_doc("developer/inline.md")["results"]["title"] == "Inline Title"
    assert ds.read_doc("developer/blank.md")["results"]["title"] == "developer/blank.md"


def test_symlinked_default_docs_root(monkeypatch, tmp_path, docs):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("", encoding="utf-8")
    try:
        os.symlink(docs, repo / "docs")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    monkeypatch.delenv("ATLAS_DOCS_ROOT", raising=False)
    ds = _load_module(monkeypatch)
    monkeypatch.setattr(ds, "_REPO_ROOT", repo)
    monkeypatch.setattr(ds, "DEFAULT_DOCS_ROOT", repo / "docs")
    assert ds.list_docs()["results"]["count"] == 3
    assert ds.read_doc("admin/mcp-servers.md")["results"]["title"] == "MCP Servers"


def test_titles_agree_across_tools(monkeypatch, docs):
    body = "intro\n" * 30 + "# Late Title\n"
    (docs / "developer" / "late.md").write_text(body, encoding="utf-8")
    (docs / "developer" / "untitled.md").write_text("no heading lateword\n", encoding="utf-8")
    ds = _load_module(monkeypatch)
    listed = {d["path"]: d["title"] for d in ds.list_docs()["results"]["documents"]}
    for path in ("developer/late.md", "developer/untitled.md"):
        assert listed[path] == ds.read_doc(path)["results"]["title"]
    assert listed["developer/late.md"] == "Late Title"
    assert listed["developer/untitled.md"] == "developer/untitled.md"
    hit = ds.search_docs("lateword")["results"]["matches"][0]
    assert hit["title"] == "developer/untitled.md"


def test_query_limits_and_original_words(monkeypatch, docs):
    ds = _load_module(monkeypatch)
    res = ds.search_docs("servers")["results"]
    assert res["matches"][0]["matched_terms"] == ["servers"]  # not the folded "server"
    assert ds.search_docs("how do servers")["results"]["terms"] == ["servers"]
    long_query = "mcp " + "x" * 1000
    assert len(ds.search_docs(long_query)["results"]["query"]) == ds.MAX_QUERY_CHARS
    # Huge queries are truncated to bounded work instead of scanning every word.
    words = " ".join(f"word{i}" for i in range(1000))
    assert ds.search_docs(words + " mcp")["results"]["matches"] == []
    assert ds.search_docs("mcp " + words)["results"]["matches"]


def test_read_doc_os_error_hides_absolute_path(monkeypatch, docs):
    ds = _load_module(monkeypatch)

    def boom(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(docs / "admin" / "mcp-servers.md"))

    monkeypatch.setattr(ds.Path, "read_text", boom)
    err = ds.read_doc("admin/mcp-servers.md")["results"]["error"]
    assert err == "Error reading document admin/mcp-servers.md: Permission denied"
    assert str(docs) not in err


def test_search_folds_plurals(monkeypatch, docs):
    ds = _load_module(monkeypatch)
    res = ds.search_docs("server")["results"]
    assert res["matches"][0]["path"] == "admin/mcp-servers.md"
    assert "server" in res["matches"][0]["matched_terms"]


def test_missing_docs_root_errors_for_every_tool(monkeypatch, tmp_path):
    monkeypatch.setenv("ATLAS_DOCS_ROOT", str(tmp_path / "nope"))
    ds = _load_module(monkeypatch)
    for result in (ds.search_docs("mcp"), ds.read_doc("README.md"), ds.list_docs()):
        assert "atlas_docs_root" in result["results"]["error"].lower()


def test_packaged_install_without_override_reports_missing_root(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLAS_DOCS_ROOT", raising=False)
    ds = _load_module(monkeypatch)
    # Simulate site-packages: no pyproject.toml beside the would-be docs folder.
    monkeypatch.setattr(ds, "_REPO_ROOT", tmp_path)
    (tmp_path / "docs").mkdir()
    assert "atlas_docs_root" in ds.list_docs()["results"]["error"].lower()


# --- read_doc --------------------------------------------------------------

def test_read_doc_full_and_range(monkeypatch, docs):
    ds = _load_module(monkeypatch)
    full = ds.read_doc("admin/mcp-servers.md")["results"]
    assert full["title"] == "MCP Servers"
    assert full["start_line"] == 1
    assert full["end_line"] == full["total_lines"]
    assert full["truncated"] is False

    part = ds.read_doc("admin/mcp-servers.md", start_line=5, max_lines=1)["results"]
    assert part["content"] == "## Authentication"
    assert part["truncated"] is True

    past_end = ds.read_doc("admin/mcp-servers.md", start_line=999)["results"]
    assert "past the end" in past_end["error"]


@pytest.mark.parametrize(
    "path,fragment",
    [
        ("../secret.md", "outside"),
        ("admin/../../secret.md", "outside"),
        ("admin/notes.txt", "markdown"),
        ("admin/missing.md", "not found"),
    ],
)
def test_read_doc_rejects_bad_paths(monkeypatch, docs, path, fragment):
    ds = _load_module(monkeypatch)
    assert fragment in ds.read_doc(path)["results"]["error"].lower()


def test_read_doc_rejects_symlink_escape(monkeypatch, docs, tmp_path):
    link = docs / "admin" / "link.md"
    try:
        os.symlink(tmp_path / "secret.md", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    ds = _load_module(monkeypatch)
    assert "outside" in ds.read_doc("admin/link.md")["results"]["error"].lower()
    paths = [d["path"] for d in ds.list_docs()["results"]["documents"]]
    assert "admin/link.md" not in paths


# --- list_docs -------------------------------------------------------------

def test_list_docs_all_and_by_section(monkeypatch, docs):
    ds = _load_module(monkeypatch)
    everything = ds.list_docs()["results"]
    assert {d["path"] for d in everything["documents"]} == {
        "README.md", "admin/mcp-servers.md", "developer/sampling.md",
    }

    admin = ds.list_docs(section="admin")["results"]["documents"]
    assert admin == [{"path": "admin/mcp-servers.md", "title": "MCP Servers"}]

    archive = ds.list_docs(section="archive")["results"]["documents"]
    assert [d["path"] for d in archive] == ["archive/old-auth-plan.md"]


# --- example config + end-to-end over stdio --------------------------------

def test_example_config_points_at_server():
    cfg_path = REPO_ROOT / "atlas" / "config" / "mcp-example-configs" / "mcp-docs_search.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))["docs_search"]
    assert cfg["command"] == ["python", "mcp/docs_search/main.py"]
    assert (REPO_ROOT / cfg["cwd"] / cfg["command"][1]).is_file()


async def test_stdio_server_end_to_end():
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    env = {k: v for k, v in os.environ.items() if k != "ATLAS_DOCS_ROOT"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(REPO_ROOT), env.get("PYTHONPATH")]))
    transport = StdioTransport(
        command=sys.executable,
        args=[str(SERVER_SCRIPT)],
        env=env,
        cwd=str(REPO_ROOT / "atlas"),
    )
    async with Client(transport) as client:
        tools = {t.name for t in await client.list_tools()}
        assert {"search_docs", "read_doc", "list_docs"} <= tools

        found = await client.call_tool("search_docs", {"query": "mcp servers configuration", "limit": 5})
        matches = found.structured_content["results"]["matches"]
        assert matches, "expected the repository docs/ folder to match"
        top_path = matches[0]["path"]

        read = await client.call_tool("read_doc", {"path": top_path, "max_lines": 5})
        assert read.structured_content["results"]["path"] == top_path
        assert read.structured_content["results"]["content"]
