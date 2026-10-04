"""Shared data-source, response, and citation models for RAG integrations."""

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# Response shapes a v2 RAG backend can return. ``raw`` hands back retrieved
# evidence for the caller's LLM to reason over; ``synthesized`` hands back an
# answer the backend's own LLM composed.
RAG_MODE_RAW = "raw"
RAG_MODE_SYNTHESIZED = "synthesized"
RAG_MODES = frozenset({RAG_MODE_RAW, RAG_MODE_SYNTHESIZED})


class DataSource(BaseModel):
    """Represents a RAG data source with compliance information."""
    id: str
    label: str
    compliance_level: str = "CUI"
    description: str = ""
    # Advertised by v2 discovery so a backend can declare, per source, which
    # contract it speaks. Absent means v1 (see docs/admin/external-rag-api.md).
    api_version: Optional[str] = None


class Section(BaseModel):
    """A relevant section/snippet from a source document.

    Mirrors the ATLAS-RAG OpenAPI ``Section`` shape. The v0.8.0 schema
    defines only ``text`` and ``relevance``; ``section_ref`` is kept
    optional so the older v1 mock shape (which numbered sections) still
    parses during the transition.
    """
    text: str
    relevance: float
    section_ref: Optional[int] = None

    @field_validator("relevance")
    @classmethod
    def clamp_relevance(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    @field_validator("text")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        # Strip null/control bytes that can corrupt downstream markdown
        # rendering. Keep tabs and newlines — snippets are often multi-line.
        if v is None:
            return ""
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v)


class DocumentMetadata(BaseModel):
    """Metadata about a source document.

    Combines the legacy fields (``source``, ``title``, ``url``,
    ``confidence_score``, ``last_modified``) with the newest-spec fields
    (``citation``, ``document_ref``, ``sections``). ``sections`` carries
    the actual snippet text the RAG backend matched to the user query,
    so the UI can show real evidence in the expanded citation area.
    """
    source: str
    content_type: str
    confidence_score: float
    chunk_id: Optional[str] = None
    last_modified: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    citation: Optional[str] = None
    document_ref: Optional[int] = None
    sections: List[Section] = Field(default_factory=list)

    @field_validator("confidence_score")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        # Strip control characters, collapse whitespace, cap length
        cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]+", "", v).strip()
        return cleaned[:200] if cleaned else None

    @field_validator("citation")
    @classmethod
    def sanitize_citation(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v).strip()
        return cleaned[:500] if cleaned else None

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not re.match(r"^https?://", v, re.IGNORECASE):
            return None
        return v


class RAGMetadata(BaseModel):
    """Metadata about RAG query processing."""
    query_processing_time_ms: int
    total_documents_searched: int
    documents_found: List[DocumentMetadata]
    data_source_name: str
    retrieval_method: str
    query_embedding_time_ms: Optional[int] = None


class URLCitation(BaseModel):
    """A ``url_citation`` annotation returned by the RAG API.

    Mirrors the ATLAS-RAG OpenAPI ``AnnotationURLCitation`` shape:
    ``start_index``/``end_index`` are offsets into the assistant message
    ``content`` identifying the span of text the citation supports.
    """
    start_index: int
    end_index: int
    title: str
    url: str


class RAGResponse(BaseModel):
    """Combined response from RAG system including content and metadata."""
    content: str
    metadata: Optional[RAGMetadata] = None
    is_completion: bool = False  # True if content is already LLM-interpreted (from /rag/completions)
    annotations: List[URLCitation] = Field(default_factory=list)
