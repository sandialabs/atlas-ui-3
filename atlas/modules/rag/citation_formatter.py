"""Utilities for formatting RAG citations and references.

Provides functions to:
- Build LLM context messages with numbered source references
- Generate structured reference markers for frontend rendering
- Extract reference data from response content
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_MARKER_PREFIX = "<!-- RAG_REFERENCES_JSON:"
_MARKER_SUFFIX = "-->"


def format_source_list(metadata) -> List[Dict[str, Any]]:
    """Extract numbered source list from RAG metadata.

    Args:
        metadata: RAGMetadata instance or None.

    Returns:
        List of dicts with keys: index, source, content_type, confidence, chunk_id.
    """
    if metadata is None:
        return []
    docs = getattr(metadata, "documents_found", None) or []
    sources = []
    for i, doc in enumerate(docs, start=1):
        sources.append({
            "index": i,
            "source": doc.source,
            "content_type": getattr(doc, "content_type", "unknown"),
            "confidence": doc.confidence_score,
            "chunk_id": getattr(doc, "chunk_id", None),
        })
    return sources


def build_citation_context(
    rag_content: str,
    metadata,
    context_label: str,
) -> str:
    """Build an LLM system message with RAG context and citation instructions.

    When metadata contains document sources, the message instructs the LLM to
    use inline citations like [1], [2] and includes a numbered source list.

    Args:
        rag_content: Retrieved text from RAG backend.
        metadata: RAGMetadata instance or None.
        context_label: Display name of the data source.

    Returns:
        Formatted system message content string.
    """
    sources = format_source_list(metadata)

    if not sources:
        return (
            f"Retrieved context from {context_label}:\n\n"
            f"{rag_content}\n\n"
            f"Use this context to inform your response."
        )

    # Build numbered source reference block
    source_lines = []
    for s in sources:
        line = f"[{s['index']}] {s['source']}"
        if s.get("content_type"):
            line += f" ({s['content_type']})"
        if s.get("confidence"):
            line += f" — {int(s['confidence'] * 100)}% relevance"
        source_lines.append(line)

    sources_block = "\n".join(source_lines)

    return (
        f"Retrieved context from {context_label}:\n\n"
        f"{rag_content}\n\n"
        f"---\n"
        f"Sources:\n{sources_block}\n\n"
        f"IMPORTANT: When you use information from the retrieved context above, "
        f"cite the source using inline numbered references like [1], [2], etc. "
        f"corresponding to the source numbers listed above. Place citations at "
        f"the end of the relevant sentence or claim. You do not need to add a "
        f"separate references section — one will be generated automatically."
    )


def build_references_marker(metadata, display_source: str) -> str:
    """Build an HTML comment marker carrying structured citation data.

    The marker is appended to the LLM response so the frontend can parse
    it and render a styled references section.

    Args:
        metadata: RAGMetadata instance or None.
        display_source: Display name of the data source.

    Returns:
        HTML comment string, or empty string if no metadata.
    """
    sources = format_source_list(metadata)
    if not sources:
        return ""

    payload = {
        "data_source": display_source,
        "retrieval_method": getattr(metadata, "retrieval_method", "unknown"),
        "processing_time_ms": getattr(metadata, "query_processing_time_ms", 0),
        "total_searched": getattr(metadata, "total_documents_searched", 0),
        "sources": sources,
    }
    return f"{_MARKER_PREFIX}{json.dumps(payload, separators=(',', ':'))}{_MARKER_SUFFIX}"


def extract_references_json(content: str) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Extract RAG references JSON from response content.

    Looks for the <!-- RAG_REFERENCES_JSON:{...}--> marker, strips it from
    the content, and returns the parsed data alongside the clean text.

    Args:
        content: Response text possibly containing a references marker.

    Returns:
        Tuple of (clean_content, references_dict_or_None).
    """
    pattern = re.compile(
        re.escape(_MARKER_PREFIX) + r"(.+?)" + re.escape(_MARKER_SUFFIX),
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        return content, None

    try:
        refs = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return content, None

    clean = content[: match.start()].rstrip()
    return clean, refs
