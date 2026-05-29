#!/usr/bin/env python3
"""ATLAS RAG API Mock Service (OpenAPI v0.3.0.dev1+).

Implements the newest ATLAS RAG API shape:

  - GET  /api/v1/discover/datasources?role=read|write&as_user=<user>
  - POST /api/v1/rag/completions?as_user=<user>

Request body (RagRequest):

    {"messages": [...], "stream": false, "corpora": "<id>" | ["<id>", ...]}

Response body (RagResponse):

    {
      "message":  {"role": "assistant", "content": "..."},
      "metadata": {
        "response_time": <int seconds>,
        "references": [
          {
            "citation": "IEEE format" | null,
            "document_ref": 1,
            "filename": "doc.pdf",
            "sections": [
              {"section_ref": 1, "text": "snippet...", "relevance": 0.92},
              ...
            ]
          },
          ...
        ]
      }
    }

Mock data is loaded from mock_data.json next to this file.
"""

import json
import logging
import os
import re
import time
import uuid  # noqa: F401  (kept available for future seeded ids if needed)
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

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
    title="ATLAS RAG API",
    description="Generates RAG response based on user query",
    version="0.3.0.dev1",
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
# Pydantic Models (OpenAPI v0.3.0.dev1+)
# ------------------------------------------------------------------------------

class DataSource(BaseModel):
    id: str = Field(..., description="Unique identifier")
    label: str = Field(..., description="Label")
    compliance_level: str = Field("CUI", description="Compliance level of data source")
    description: str = Field("", description="Description")


class MessageInput(BaseModel):
    role: Literal["user", "system", "assistant", "tool", "developer"]
    content: str


class MessageOutput(BaseModel):
    role: Literal["system", "user", "assistant", "tool", "developer"] = "assistant"
    content: str


class Section(BaseModel):
    section_ref: int = Field(..., description="The section ID component of the in-text citation.")
    text: str = Field(..., description="Relevant text snippet from source document.")
    relevance: float = Field(..., description="Cosine similarity score of the snippet to the user query.")


class Reference(BaseModel):
    citation: Optional[str] = Field(None, description="Citation in IEEE format.")
    document_ref: int = Field(..., description="The document reference number for intext citations.")
    filename: str = Field(..., description="Filename of the source document.")
    sections: List[Section] = Field(..., description="Relevant sections from the source document.")


class RagMetadata(BaseModel):
    response_time: int = Field(..., description="How long it took for the response to be generated, in seconds.")
    references: Optional[List[Reference]] = Field(
        ..., description="List of references used to generate response."
    )


class RagRequest(BaseModel):
    messages: List[MessageInput] = Field(..., description="Message history")
    stream: bool = Field(False, description="Stream response")
    corpora: Union[str, List[str]] = Field(..., description="Corpora")


class RagResponse(BaseModel):
    message: MessageOutput
    metadata: RagMetadata


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


def grep_search(query: str, text: str, context_chars: int = 240) -> List[Tuple[str, float]]:
    """Return ``(snippet, score)`` tuples in ``text`` that match query tokens.

    Score is the fraction of non-stopword query tokens that appear in the
    snippet — used directly as the ``Section.relevance`` value in the mock.
    """
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


def _ieee_citation(document_ref: int, title: str, filename: str, url: Optional[str]) -> str:
    """Best-effort IEEE-style citation string for the mock.

    Real backends will produce a properly-formatted citation; the mock
    just composes something recognizable so the frontend has a non-empty
    ``citation`` field to render.
    """
    parts = [f"[{document_ref}]"]
    if title:
        parts.append(f'"{title}",')
    parts.append(filename)
    if url:
        parts.append(f"available: {url}")
    return " ".join(parts)


def search_corpus_for_references(
    query: str,
    corpus_id: str,
    top_k: int,
    start_doc_ref: int,
) -> List[Reference]:
    """Build Reference objects for matching documents in ``corpus_id``.

    Each matching document yields one Reference; its grep hits become
    Section snippets with ``section_ref`` numbering starting at 1.
    """
    if corpus_id not in DATA_SOURCES:
        return []

    corpus = DATA_SOURCES[corpus_id]
    references: List[Reference] = []
    document_ref = start_doc_ref

    scored_docs: List[Tuple[Dict[str, Any], List[Tuple[str, float]], float]] = []
    for doc in corpus["documents"]:
        hits = grep_search(query, doc["content"])
        if not hits:
            continue
        top_score = hits[0][1]
        scored_docs.append((doc, hits, top_score))

    scored_docs.sort(key=lambda t: -t[2])
    scored_docs = scored_docs[:top_k]

    for doc, hits, _top_score in scored_docs:
        sections = [
            Section(
                section_ref=section_ref,
                text=snippet,
                relevance=round(score, 4),
            )
            for section_ref, (snippet, score) in enumerate(hits, start=1)
        ]
        title = doc.get("title") or doc.get("id") or corpus_id
        filename = doc.get("filename") or f"{doc.get('id', 'doc')}.txt"
        references.append(
            Reference(
                citation=_ieee_citation(document_ref, title, filename, doc.get("url")),
                document_ref=document_ref,
                filename=filename,
                sections=sections,
            )
        )
        document_ref += 1

    return references


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

def _compose_assistant_content(
    corpora_searched: List[str],
    references: List[Reference],
    user_query: str,
) -> str:
    """Build a human-readable assistant message that cites each reference.

    Citations use the ``[document_ref]`` form (e.g., ``[1]``, ``[2]``) so
    the existing frontend citation pipeline keeps working without changes.
    """
    if not references:
        return (
            f"No results found for: \"{user_query}\"\n\n"
            f"Searched in: {', '.join(corpora_searched)}\n"
            "Try different keywords or check your data source access."
        )

    parts: List[str] = []
    parts.append(
        f"Based on searching {len(corpora_searched)} data source(s), "
        f"I found {len(references)} relevant document(s):"
    )
    parts.append("")
    for ref in references:
        # One paragraph per reference, with the lead snippet inline and a
        # trailing citation marker so [N] flows naturally in the prose.
        lead_snippet = ref.sections[0].text if ref.sections else ref.filename
        parts.append(f"- {lead_snippet} [{ref.document_ref}]")
    parts.append("")
    parts.append(
        "Sources: "
        + ", ".join(sorted({c for c in corpora_searched}))
    )
    return "\n".join(parts)


# ------------------------------------------------------------------------------
# API Endpoints
# ------------------------------------------------------------------------------

@app.get("/api/v1/discover/datasources", response_model=List[DataSource])
async def discover_data_sources(
    role: Literal["read", "write"] = Query("read"),
    as_user: Optional[str] = Query(None, description="User ID to impersonate"),
):
    """Discover data sources accessible by a user.

    Per spec, returns a bare list of ``DataSource`` objects. The mock
    treats ``read`` and ``write`` identically.
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
    return accessible


@app.post("/api/v1/rag/completions", response_model=RagResponse)
async def rag_completions(
    request: RagRequest,
    as_user: Optional[str] = Query(None, description="User ID to impersonate"),
):
    """Query RAG with grep-based search and return references with sections."""
    start_time = time.time()

    user = as_user or ""
    logger.info("---------- RAG query ----------")
    logger.info(
        "RAG query user=%s corpora=%s stream=%s message_count=%d",
        user,
        request.corpora,
        request.stream,
        len(request.messages),
    )

    if isinstance(request.corpora, str):
        corpora_to_search = [request.corpora]
    else:
        corpora_to_search = list(request.corpora)

    for corpus in corpora_to_search:
        if corpus not in DATA_SOURCES:
            raise HTTPException(status_code=404, detail=f"Corpus '{corpus}' not found")
        if not can_access_corpus(user, corpus):
            raise HTTPException(status_code=403, detail=f"Access denied to '{corpus}'")

    user_query = ""
    for m in reversed(request.messages):
        if m.role == "user":
            user_query = m.content or ""
            break
    logger.info("RAG user query: %r", user_query)

    references: List[Reference] = []
    next_doc_ref = 1
    for corpus in corpora_to_search:
        corpus_refs = search_corpus_for_references(
            user_query, corpus, top_k=4, start_doc_ref=next_doc_ref,
        )
        references.extend(corpus_refs)
        next_doc_ref += len(corpus_refs)

    content = _compose_assistant_content(corpora_to_search, references, user_query)

    # Spec describes response_time as an integer number of seconds.
    response_time_seconds = max(1, int(time.time() - start_time + 1))

    return RagResponse(
        message=MessageOutput(role="assistant", content=content),
        metadata=RagMetadata(
            response_time=response_time_seconds,
            references=references or None,
        ),
    )


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/")
async def root():
    return {
        "service": "ATLAS RAG API Mock",
        "version": "0.3.0.dev1",
        "openapi": "v0.3.0.dev1+",
        "data_sources": list(DATA_SOURCES.keys()),
        "test_users": list(USERS_GROUPS_DB.keys()),
        "endpoints": {
            "GET /api/v1/discover/datasources?role=read|write&as_user=<user>":
                "List accessible data sources",
            "POST /api/v1/rag/completions?as_user=<user>":
                "Search and query (returns RagResponse with references/sections)",
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
