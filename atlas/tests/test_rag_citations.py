"""Tests for RAG citation and reference formatting (GH #443).

Covers:
- DocumentMetadata title/url fields
- _build_citation_instructions() prompt generation
- _format_rag_references() numbered reference section
- _format_rag_metadata() backward-compat wrapper
"""

import pytest

from atlas.modules.rag.client import DocumentMetadata, RAGMetadata


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_metadata(docs=None, **kwargs):
    """Build a RAGMetadata with sensible defaults."""
    defaults = dict(
        query_processing_time_ms=150,
        total_documents_searched=100,
        documents_found=docs or [],
        data_source_name="technical-docs",
        retrieval_method="similarity",
    )
    defaults.update(kwargs)
    return RAGMetadata(**defaults)


def _make_doc(**kwargs):
    """Build a DocumentMetadata with sensible defaults."""
    defaults = dict(
        source="tech-corpus",
        content_type="atlas-search",
        confidence_score=0.92,
    )
    defaults.update(kwargs)
    return DocumentMetadata(**defaults)


# We need a minimal caller instance to invoke the static/instance methods.
# Since the methods under test are static or only use `self` for backward
# compat, we can import and call them directly on the class.

from atlas.modules.llm.litellm_caller import LiteLLMCaller


# ---------------------------------------------------------------------------
# DocumentMetadata title/url
# ---------------------------------------------------------------------------

class TestDocumentMetadataFields:
    def test_title_and_url_optional(self):
        doc = DocumentMetadata(
            source="corpus-1",
            content_type="text",
            confidence_score=0.8,
        )
        assert doc.title is None
        assert doc.url is None

    def test_title_and_url_set(self):
        doc = DocumentMetadata(
            source="corpus-1",
            content_type="text",
            confidence_score=0.8,
            title="API Auth Guide",
            url="https://docs.example.com/auth",
        )
        assert doc.title == "API Auth Guide"
        assert doc.url == "https://docs.example.com/auth"


# ---------------------------------------------------------------------------
# _build_citation_instructions
# ---------------------------------------------------------------------------

class TestBuildCitationInstructions:
    def test_empty_when_no_documents(self):
        meta = _make_metadata(docs=[])
        result = LiteLLMCaller._build_citation_instructions(meta)
        assert result == ""

    def test_empty_for_non_metadata_input(self):
        result = LiteLLMCaller._build_citation_instructions("not metadata")
        assert result == ""

    def test_numbered_source_list(self):
        docs = [
            _make_doc(title="Auth Guide", url="https://docs.example.com/auth", confidence_score=0.95),
            _make_doc(title="Deploy Guide", confidence_score=0.80),
        ]
        meta = _make_metadata(docs=docs)
        result = LiteLLMCaller._build_citation_instructions(meta)

        assert "[1] **Auth Guide**" in result
        assert "URL: https://docs.example.com/auth" in result
        assert "Relevance: 95%" in result
        assert "[2] **Deploy Guide**" in result
        assert "Relevance: 80%" in result

    def test_fallback_label_when_no_title(self):
        docs = [_make_doc(source="my-corpus")]
        meta = _make_metadata(docs=docs)
        result = LiteLLMCaller._build_citation_instructions(meta)
        assert "[1] **my-corpus**" in result

    def test_includes_citation_instructions(self):
        docs = [_make_doc(title="Doc 1")]
        meta = _make_metadata(docs=docs)
        result = LiteLLMCaller._build_citation_instructions(meta)
        assert "cite them inline" in result.lower() or "bracketed numbers" in result.lower()

    def test_includes_last_modified(self):
        docs = [_make_doc(title="Doc 1", last_modified="2026-03-15T10:00:00Z")]
        meta = _make_metadata(docs=docs)
        result = LiteLLMCaller._build_citation_instructions(meta)
        assert "2026-03-15T10:00:00Z" in result


# ---------------------------------------------------------------------------
# _format_rag_references
# ---------------------------------------------------------------------------

class TestFormatRagReferences:
    def test_empty_when_no_documents(self):
        meta = _make_metadata(docs=[])
        result = LiteLLMCaller._format_rag_references(meta)
        assert result == ""

    def test_empty_for_non_metadata_input(self):
        result = LiteLLMCaller._format_rag_references("not metadata")
        assert result == ""

    def test_numbered_reference_list(self):
        docs = [
            _make_doc(title="Auth Guide", url="https://docs.example.com/auth", confidence_score=0.95),
            _make_doc(title="Schema Docs", confidence_score=0.87),
        ]
        meta = _make_metadata(docs=docs)
        result = LiteLLMCaller._format_rag_references(meta)

        assert "**References**" in result
        # First entry should have a markdown link
        assert "1. [Auth Guide](https://docs.example.com/auth)" in result
        assert "95% relevance" in result
        # Second entry should be plain text (no URL)
        assert "2. Schema Docs" in result
        assert "87% relevance" in result

    def test_includes_processing_footer(self):
        docs = [_make_doc(title="Doc 1")]
        meta = _make_metadata(docs=docs, data_source_name="corp-docs",
                              retrieval_method="hybrid", query_processing_time_ms=200)
        result = LiteLLMCaller._format_rag_references(meta)
        assert "corp-docs" in result
        assert "hybrid" in result
        assert "200ms" in result

    def test_source_shown_when_different_from_title(self):
        docs = [_make_doc(title="My Title", source="different-corpus")]
        meta = _make_metadata(docs=docs)
        result = LiteLLMCaller._format_rag_references(meta)
        assert "different-corpus" in result

    def test_source_not_duplicated_when_same_as_title(self):
        docs = [_make_doc(title="same-label", source="same-label")]
        meta = _make_metadata(docs=docs)
        result = LiteLLMCaller._format_rag_references(meta)
        # Source should only appear once (as the label), not repeated
        lines = result.split("\n")
        ref_line = [l for l in lines if l.startswith("1.")][0]
        assert ref_line.count("same-label") == 1


# ---------------------------------------------------------------------------
# _format_rag_metadata (backward compat wrapper)
# ---------------------------------------------------------------------------

class TestFormatRagMetadataCompat:
    def test_returns_metadata_unavailable_for_empty(self):
        meta = _make_metadata(docs=[])
        # Need an instance for the non-static wrapper
        caller = LiteLLMCaller.__new__(LiteLLMCaller)
        result = caller._format_rag_metadata(meta)
        assert result == "Metadata unavailable"

    def test_returns_references_when_docs_present(self):
        docs = [_make_doc(title="Doc A")]
        meta = _make_metadata(docs=docs)
        caller = LiteLLMCaller.__new__(LiteLLMCaller)
        result = caller._format_rag_metadata(meta)
        assert "**References**" in result
        assert "Doc A" in result
