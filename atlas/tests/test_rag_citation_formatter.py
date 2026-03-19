"""Tests for RAG citation formatting utilities."""

import json

import pytest

from atlas.modules.rag.client import DocumentMetadata, RAGMetadata, RAGResponse


def _sample_metadata():
    return RAGMetadata(
        query_processing_time_ms=150,
        total_documents_searched=100,
        documents_found=[
            DocumentMetadata(
                source="technical-manual-v3.pdf",
                content_type="pdf",
                confidence_score=0.95,
                chunk_id="chunk-42",
            ),
            DocumentMetadata(
                source="safety-guidelines.docx",
                content_type="docx",
                confidence_score=0.82,
            ),
        ],
        data_source_name="engineering-docs",
        retrieval_method="similarity",
    )


class TestFormatSourceList:
    def test_extracts_source_info(self):
        from atlas.modules.rag.citation_formatter import format_source_list

        meta = _sample_metadata()
        sources = format_source_list(meta)
        assert len(sources) == 2
        assert sources[0]["index"] == 1
        assert sources[0]["source"] == "technical-manual-v3.pdf"
        assert sources[0]["confidence"] == 0.95
        assert sources[1]["index"] == 2

    def test_empty_metadata(self):
        from atlas.modules.rag.citation_formatter import format_source_list

        meta = RAGMetadata(
            query_processing_time_ms=0,
            total_documents_searched=0,
            documents_found=[],
            data_source_name="empty",
            retrieval_method="none",
        )
        assert format_source_list(meta) == []

    def test_none_metadata(self):
        from atlas.modules.rag.citation_formatter import format_source_list

        assert format_source_list(None) == []


class TestBuildCitationContext:
    def test_includes_numbered_sources(self):
        from atlas.modules.rag.citation_formatter import build_citation_context

        meta = _sample_metadata()
        result = build_citation_context(
            rag_content="Some retrieved text",
            metadata=meta,
            context_label="engineering-docs",
        )
        assert "[1]" in result
        assert "[2]" in result
        assert "technical-manual-v3.pdf" in result
        assert "safety-guidelines.docx" in result

    def test_includes_citation_instructions(self):
        from atlas.modules.rag.citation_formatter import build_citation_context

        meta = _sample_metadata()
        result = build_citation_context("text", meta, "docs")
        assert "cite" in result.lower() or "[1]" in result

    def test_no_metadata_returns_plain_context(self):
        from atlas.modules.rag.citation_formatter import build_citation_context

        result = build_citation_context("plain text", None, "docs")
        assert "plain text" in result
        assert "[1]" not in result

    def test_empty_documents_returns_plain_context(self):
        from atlas.modules.rag.citation_formatter import build_citation_context

        meta = RAGMetadata(
            query_processing_time_ms=0,
            total_documents_searched=0,
            documents_found=[],
            data_source_name="empty",
            retrieval_method="none",
        )
        result = build_citation_context("text", meta, "docs")
        assert "cite" not in result.lower()


class TestBuildReferencesMarker:
    def test_produces_html_comment(self):
        from atlas.modules.rag.citation_formatter import build_references_marker

        meta = _sample_metadata()
        marker = build_references_marker(meta, "engineering-docs")
        assert marker.startswith("<!-- RAG_REFERENCES_JSON:")
        assert marker.endswith("-->")

    def test_valid_json_payload(self):
        from atlas.modules.rag.citation_formatter import build_references_marker

        meta = _sample_metadata()
        marker = build_references_marker(meta, "engineering-docs")
        json_str = marker.replace("<!-- RAG_REFERENCES_JSON:", "").replace("-->", "")
        data = json.loads(json_str)
        assert "sources" in data
        assert len(data["sources"]) == 2
        assert data["data_source"] == "engineering-docs"
        assert data["retrieval_method"] == "similarity"

    def test_none_metadata_returns_empty_string(self):
        from atlas.modules.rag.citation_formatter import build_references_marker

        assert build_references_marker(None, "x") == ""


class TestExtractReferencesJson:
    def test_extracts_from_content(self):
        from atlas.modules.rag.citation_formatter import (
            build_references_marker,
            extract_references_json,
        )

        meta = _sample_metadata()
        marker = build_references_marker(meta, "docs")
        content = f"Some response text.\n\n{marker}"
        clean, refs = extract_references_json(content)
        assert clean.strip() == "Some response text."
        assert refs is not None
        assert len(refs["sources"]) == 2

    def test_no_marker_returns_none(self):
        from atlas.modules.rag.citation_formatter import extract_references_json

        clean, refs = extract_references_json("No citations here.")
        assert clean == "No citations here."
        assert refs is None
