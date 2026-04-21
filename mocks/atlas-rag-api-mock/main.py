#!/usr/bin/env python3
"""
ATLAS RAG API Mock Service (OpenAPI v0.3.x).

Implements the ATLAS RAG API shape described in the public OpenAPI spec:

  - GET  /api/v1/discover/datasources?role=read|write&as_user=<user>
  - POST /api/v1/rag/completions?as_user=<user>

Responses follow the OpenAI chat.completion schema, with an extra
``rag_metadata`` field and per-message ``annotations`` (url_citation)
that link specific character ranges in the assistant content to the
source documents they were derived from.

Mock data is loaded from mock_data.json next to this file.
"""

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------------
# Load Mock Data from JSON
# ------------------------------------------------------------------------------

def load_mock_data() -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """Load mock data from mock_data.json file."""
    data_file = Path(__file__).parent / "mock_data.json"

    if not data_file.exists():
        logger.error("Mock data file not found: %s", data_file)
        raise FileNotFoundError(f"Mock data file not found: {data_file}")

    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    data_sources = data.get("data_sources", {})
    users_groups = data.get("users_groups", {})

    logger.info(
        "Loaded mock data: %d data sources, %d users",
        len(data_sources),
        len(users_groups),
    )

    return data_sources, users_groups


DATA_SOURCES, USERS_GROUPS_DB = load_mock_data()


# ------------------------------------------------------------------------------
# Static Token Verifier
# ------------------------------------------------------------------------------

class StaticTokenVerifier:
    """Static token verifier for development/testing."""

    def __init__(self, tokens: Dict[str, Dict[str, Any]]):
        self.tokens = tokens

    def verify(self, token: str) -> Optional[Dict[str, Any]]:
        return self.tokens.get(token)


shared_key = (
    os.getenv("ATLAS_RAG_SHARED_KEY")
    or os.getenv("atlas_rag_shared_key")
    or "test-atlas-rag-token"
)

verifier = StaticTokenVerifier(
    tokens={
        shared_key: {
            "user_id": "atlas-ui",
            "client_id": "atlas-ui-backend",
            "scopes": ["read", "write"],
        }
    }
)


# ------------------------------------------------------------------------------
# FastAPI App
# ------------------------------------------------------------------------------

app = FastAPI(
    title="ATLAS RAG API Mock",
    description="Mock aligned with the ATLAS RAG OpenAPI v0.3.x spec",
    version="0.3.0",
)

PUBLIC_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def verify_token_middleware(request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or invalid Authorization header"},
        )

    token = auth_header[7:]
    if verifier.verify(token) is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid bearer token"})

    return await call_next(request)


# ------------------------------------------------------------------------------
# Pydantic Models (OpenAPI v0.3.x)
# ------------------------------------------------------------------------------

class DataSource(BaseModel):
    id: str = Field(..., description="Unique identifier")
    label: str = Field(..., description="Label")
    compliance_level: str = Field("CUI", description="Compliance level of data source")
    description: str = Field("", description="Description")


class DiscoverDataSourcesResponse(BaseModel):
    data_sources: List[DataSource]


class AnnotationURLCitation(BaseModel):
    end_index: int
    start_index: int
    title: str
    url: str


class Annotation(BaseModel):
    type: Literal["url_citation"] = "url_citation"
    url_citation: AnnotationURLCitation


class ChatCompletionMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: Optional[str] = None
    refusal: Optional[str] = None
    annotations: Optional[List[Annotation]] = None


class Choice(BaseModel):
    finish_reason: Literal[
        "stop", "length", "tool_calls", "content_filter", "function_call"
    ] = "stop"
    index: int = 0
    message: ChatCompletionMessage


class DocumentMetadata(BaseModel):
    data_source: DataSource
    text: str
    id: Optional[int] = None
    content_type: str = "atlas-search"
    confidence_score: float
    last_modified: Optional[str] = None


class RagMetadata(BaseModel):
    query_processing_time_ms: int
    documents_found: List[DocumentMetadata]
    data_sources: List[DataSource]
    retrieval_method: str = "similarity"


class CompletionUsage(BaseModel):
    completion_tokens: int
    prompt_tokens: int
    total_tokens: int


class RagRequest(BaseModel):
    messages: List[Dict[str, Any]] = Field(..., description="Message history")
    stream: bool = Field(False, description="Stream response")
    model: str = Field("openai/gpt-oss-120b", description="LLM used to generate response")
    hybrid_search_kwargs: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional arguments to be passed to HybridSearch",
    )
    shirty_api_key: Optional[str] = None
    search_api_key: Optional[str] = None
    as_user: Optional[str] = None


class RagResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    choices: List[Choice]
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    object: Literal["chat.completion"] = "chat.completion"
    service_tier: Optional[str] = None
    system_fingerprint: Optional[str] = None
    usage: Optional[CompletionUsage] = None
    rag_metadata: RagMetadata


# ------------------------------------------------------------------------------
# Search Helpers
# ------------------------------------------------------------------------------

STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "between", "under",
    "and", "or", "but", "if", "then", "else", "when", "where", "why",
    "how", "what", "which", "who", "whom", "this", "that", "these",
    "those", "it", "its", "i", "me", "my", "we", "our", "you", "your",
}


def grep_search(query: str, text: str, context_chars: int = 200) -> List[Tuple[str, float]]:
    """Return snippets in ``text`` that match any non-stopword token in ``query``."""
    if not query or not text:
        return []

    query_words = [
        w.lower() for w in re.findall(r"\w+", query) if w.lower() not in STOP_WORDS
    ]
    if not query_words:
        return []

    results: List[Tuple[str, float]] = []
    lines = text.split("\n")

    for i, line in enumerate(lines):
        line_lower = line.lower()
        matches = sum(1 for w in query_words if w in line_lower)
        if matches == 0:
            continue
        score = matches / len(query_words)

        start_line = max(0, i - 1)
        end_line = min(len(lines), i + 2)
        snippet = "\n".join(lines[start_line:end_line]).strip()
        if len(snippet) > context_chars:
            snippet = snippet[:context_chars] + "..."
        results.append((snippet, score))

    results.sort(key=lambda x: -x[1])

    seen = set()
    unique: List[Tuple[str, float]] = []
    for snippet, score in results:
        key = snippet[:50]
        if key not in seen:
            seen.add(key)
            unique.append((snippet, score))
    return unique[:5]


def _make_data_source(corpus_id: str) -> DataSource:
    corpus = DATA_SOURCES[corpus_id]
    return DataSource(
        id=corpus_id,
        label=corpus.get("name", corpus_id),
        compliance_level=corpus.get("compliance_level", "CUI"),
        description=corpus.get("description", ""),
    )


def search_corpus(
    query: str, corpus_id: str, top_k: int
) -> List[Tuple[DocumentMetadata, Optional[str], Optional[str]]]:
    """Return ``(DocumentMetadata, title, url)`` tuples for top matches in ``corpus_id``."""
    if corpus_id not in DATA_SOURCES:
        return []

    corpus = DATA_SOURCES[corpus_id]
    data_source = _make_data_source(corpus_id)

    scored: List[Tuple[DocumentMetadata, Optional[str], Optional[str], float]] = []
    chunk_id = 1
    for doc in corpus["documents"]:
        for snippet, score in grep_search(query, doc["content"]):
            metadata = DocumentMetadata(
                data_source=data_source,
                text=snippet,
                id=chunk_id,
                content_type="atlas-search",
                confidence_score=round(score, 4),
                last_modified=doc.get("last_modified"),
            )
            scored.append((metadata, doc.get("title"), doc.get("url"), score))
            chunk_id += 1

    scored.sort(key=lambda x: -x[3])
    return [(m, t, u) for m, t, u, _ in scored[:top_k]]


# ------------------------------------------------------------------------------
# Authorization Helpers
# ------------------------------------------------------------------------------

def get_accessible_corpora(user_name: str) -> List[DataSource]:
    """Return data sources accessible by ``user_name``."""
    user_groups = set(USERS_GROUPS_DB.get(user_name, []))
    accessible: List[DataSource] = []

    for corpus_id, corpus in DATA_SOURCES.items():
        required = set(corpus.get("required_groups", []))
        if not required or (user_groups & required):
            accessible.append(_make_data_source(corpus_id))

    return accessible


def can_access_corpus(user_name: str, corpus_id: str) -> bool:
    if corpus_id not in DATA_SOURCES:
        return False
    required = set(DATA_SOURCES[corpus_id].get("required_groups", []))
    if not required:
        return True
    user_groups = set(USERS_GROUPS_DB.get(user_name, []))
    return bool(user_groups & required)


# ------------------------------------------------------------------------------
# Response Composition
# ------------------------------------------------------------------------------

def _compose_answer_with_citations(
    corpora_searched: List[str],
    docs_with_meta: List[Tuple[DocumentMetadata, Optional[str], Optional[str]]],
    user_query: str,
) -> Tuple[str, List[Annotation]]:
    """Build assistant content and url_citation annotations pointing into it.

    Each annotation's ``start_index``/``end_index`` bounds the exact characters
    in the generated content that came from the referenced document.
    """
    if not docs_with_meta:
        content = (
            f"No results found for: \"{user_query}\"\n\n"
            f"Searched in: {', '.join(corpora_searched)}\n"
            "Try different keywords or check your data source access."
        )
        return content, []

    parts: List[str] = []
    annotations: List[Annotation] = []

    intro = (
        f"Based on searching {len(corpora_searched)} data source(s), "
        f"I found {len(docs_with_meta)} relevant result(s):\n\n"
    )
    parts.append(intro)
    cursor = len(intro)

    for idx, (doc, title, url) in enumerate(docs_with_meta, start=1):
        title_str = title or doc.data_source.label
        header = f"[{idx}] {title_str}\n"
        body = doc.text
        citation_marker = f" [{idx}]"

        parts.append(header)
        cursor += len(header)

        body_start = cursor
        parts.append(body)
        cursor += len(body)
        body_end = cursor

        parts.append(citation_marker)
        cursor += len(citation_marker)
        parts.append("\n\n")
        cursor += 2

        annotations.append(
            Annotation(
                type="url_citation",
                url_citation=AnnotationURLCitation(
                    start_index=body_start,
                    end_index=body_end,
                    title=title_str,
                    url=url or "",
                ),
            )
        )

    footer = (
        f"These results are from: "
        f"{', '.join(sorted({d.data_source.id for d, _, _ in docs_with_meta}))}"
    )
    parts.append(footer)

    return "".join(parts), annotations


# ------------------------------------------------------------------------------
# API Endpoints
# ------------------------------------------------------------------------------

@app.get("/api/v1/discover/datasources", response_model=DiscoverDataSourcesResponse)
async def discover_data_sources(
    role: Literal["read", "write"] = Query("read"),
    as_user: Optional[str] = Query(None, description="User ID to impersonate"),
):
    """Discover data sources accessible by a user.

    The mock treats ``read`` and ``write`` identically; a production impl would
    filter further by role.
    """
    user = as_user or ""
    logger.info("Discovery request: user=%s role=%s", user, role)
    accessible = get_accessible_corpora(user)
    logger.info(
        "User %s can access %d data sources (role=%s)",
        user,
        len(accessible),
        role,
    )
    return DiscoverDataSourcesResponse(data_sources=accessible)


@app.post("/api/v1/rag/completions", response_model=RagResponse)
async def rag_completions(
    request: RagRequest,
    as_user: Optional[str] = Query(None, description="User ID to impersonate"),
):
    """Query RAG with grep-based search."""
    start_time = time.time()

    user = as_user or request.as_user or ""
    logger.info("---------- RAG query ----------")
    logger.info(
        "RAG query user=%s model=%s hybrid_kwargs=%s",
        user,
        request.model,
        request.hybrid_search_kwargs,
    )

    top_k = int(request.hybrid_search_kwargs.get("top_k", 4))
    requested_corpora = (
        request.hybrid_search_kwargs.get("corpora")
        or request.hybrid_search_kwargs.get("data_sources")
    )
    if requested_corpora is None:
        corpora_to_search = [ds.id for ds in get_accessible_corpora(user)]
    else:
        corpora_to_search = list(requested_corpora)

    for corpus in corpora_to_search:
        if corpus not in DATA_SOURCES:
            raise HTTPException(status_code=404, detail=f"Corpus '{corpus}' not found")
        if not can_access_corpus(user, corpus):
            raise HTTPException(status_code=403, detail=f"Access denied to '{corpus}'")

    user_query = ""
    for m in reversed(request.messages):
        if m.get("role") == "user":
            c = m.get("content", "")
            if isinstance(c, list):
                user_query = " ".join(
                    p.get("text", "")
                    for p in c
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            else:
                user_query = c or ""
            break
    logger.info("RAG user query: %r", user_query)

    docs_with_meta: List[Tuple[DocumentMetadata, Optional[str], Optional[str]]] = []
    for corpus in corpora_to_search:
        docs_with_meta.extend(search_corpus(user_query, corpus, top_k))

    docs_with_meta.sort(key=lambda x: -x[0].confidence_score)
    docs_with_meta = docs_with_meta[:top_k]

    content, annotations = _compose_answer_with_citations(
        corpora_to_search, docs_with_meta, user_query
    )

    processing_time = int((time.time() - start_time) * 1000) + 20

    return RagResponse(
        model=request.model,
        choices=[
            Choice(
                index=0,
                finish_reason="stop",
                message=ChatCompletionMessage(
                    role="assistant",
                    content=content,
                    annotations=annotations or None,
                ),
            )
        ],
        rag_metadata=RagMetadata(
            query_processing_time_ms=processing_time,
            documents_found=[m for m, _, _ in docs_with_meta],
            data_sources=[
                _make_data_source(c) for c in corpora_to_search if c in DATA_SOURCES
            ],
            retrieval_method="similarity",
        ),
    )


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/")
async def root():
    return {
        "service": "ATLAS RAG API Mock",
        "version": "0.3.0",
        "openapi": "v0.3.x",
        "data_sources": list(DATA_SOURCES.keys()),
        "test_users": list(USERS_GROUPS_DB.keys()),
        "endpoints": {
            "GET /api/v1/discover/datasources?role=read|write&as_user=<user>":
                "List accessible data sources",
            "POST /api/v1/rag/completions?as_user=<user>":
                "Search and query",
            "GET /health": "Health check",
        },
    }


if __name__ == "__main__":
    port = int(os.getenv("ATLAS_RAG_MOCK_PORT", "8002"))
    print(f"Starting ATLAS RAG Mock on port {port}")
    print(f"Data sources: {list(DATA_SOURCES.keys())}")
    print(f"Test users: {list(USERS_GROUPS_DB.keys())}")
    print(f"Bearer token: {shared_key}")
    uvicorn.run(app, host="127.0.0.1", port=port)
