#!/usr/bin/env python3
"""
ATLAS RAG API Mock Service with Grep-Based Search

Provides mock endpoints that simulate the external ATLAS RAG API:
  - GET  /discover/datasources  - Discover accessible data sources
  - POST /rag/completions       - Query RAG with grep-based search

This mock searches through realistic text data using simple keyword matching.
Mock data is loaded from mock_data.json in the same directory.
"""

import json
import logging
import os
from pathlib import Path
import re
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

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

    logger.info("Loaded mock data: %d data sources, %d users",
                len(data_sources), len(users_groups))

    return data_sources, users_groups


# Load data at module level
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


# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

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
    description="Mock API with grep-based search over realistic data",
    version="2.0.0",
)

PUBLIC_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def verify_token_middleware(request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"detail": "Missing or invalid Authorization header"})

    token = auth_header[7:]
    if verifier.verify(token) is None:
        return JSONResponse(status_code=401, content={"detail": "Invalid bearer token"})

    return await call_next(request)


# ------------------------------------------------------------------------------
# Pydantic Models
# ------------------------------------------------------------------------------

class DataSourceInfo(BaseModel):
    id: str
    label: str
    compliance_level: str = "CUI"
    description: str = ""


class DataSourceDiscoveryResponse(BaseModel):
    data_sources: List[DataSourceInfo]


class ChatMessage(BaseModel):
    role: str
    content: str


class RagRequest(BaseModel):
    messages: List[ChatMessage]
    stream: bool = False
    model: str = "gpt-4"
    top_k: int = 4
    corpora: Optional[List[str]] = None


class DocumentFound(BaseModel):
    id: str
    corpus_id: str
    title: str
    text: str
    confidence_score: float
    content_type: str = "text"
    last_modified: Optional[str] = None


class RagMetadata(BaseModel):
    query_processing_time_ms: int
    documents_found: List[DocumentFound]
    data_sources: List[str]
    retrieval_method: str = "keyword-search"


class RagResponseChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class RagResponse(BaseModel):
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[RagResponseChoice]
    rag_metadata: Optional[RagMetadata] = None


# ------------------------------------------------------------------------------
# Search Functions
# ------------------------------------------------------------------------------

def grep_search(query: str, text: str, context_chars: int = 200) -> List[Tuple[str, float]]:
    """
    Search for query terms in text and return matching snippets with scores.
    Returns list of (snippet, score) tuples.
    """
    if not query or not text:
        return []

    # Tokenize query into words (ignore common stop words)
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
                  "have", "has", "had", "do", "does", "did", "will", "would", "could",
                  "should", "may", "might", "must", "shall", "can", "to", "of", "in",
                  "for", "on", "with", "at", "by", "from", "as", "into", "through",
                  "during", "before", "after", "above", "below", "between", "under",
                  "and", "or", "but", "if", "then", "else", "when", "where", "why",
                  "how", "what", "which", "who", "whom", "this", "that", "these",
                  "those", "it", "its", "i", "me", "my", "we", "our", "you", "your"}

    query_words = [w.lower() for w in re.findall(r'\w+', query) if w.lower() not in stop_words]

    if not query_words:
        return []

    results = []
    lines = text.split('\n')

    for i, line in enumerate(lines):
        line_lower = line.lower()
        matches = sum(1 for word in query_words if word in line_lower)

        if matches > 0:
            # Calculate score based on match ratio and position
            score = matches / len(query_words)

            # Build context (include surrounding lines)
            start_line = max(0, i - 1)
            end_line = min(len(lines), i + 2)
            snippet = '\n'.join(lines[start_line:end_line]).strip()

            # Truncate if too long
            if len(snippet) > context_chars:
                snippet = snippet[:context_chars] + "..."

            results.append((snippet, score))

    # Sort by score and deduplicate
    results.sort(key=lambda x: -x[1])
    seen = set()
    unique_results = []
    for snippet, score in results:
        snippet_key = snippet[:50]  # Use first 50 chars as key
        if snippet_key not in seen:
            seen.add(snippet_key)
            unique_results.append((snippet, score))

    return unique_results[:5]  # Return top 5 matches


def search_corpus(query: str, corpus_id: str, top_k: int = 4) -> List[DocumentFound]:
    """Search a corpus for relevant documents."""
    if corpus_id not in DATA_SOURCES:
        return []

    corpus = DATA_SOURCES[corpus_id]
    all_results = []

    for doc in corpus["documents"]:
        matches = grep_search(query, doc["content"])

        for snippet, score in matches:
            all_results.append(DocumentFound(
                id=doc["id"],
                corpus_id=corpus_id,
                title=doc["title"],
                text=snippet,
                confidence_score=round(score, 2),
                content_type="text",
                last_modified=doc.get("last_modified"),
            ))

    # Sort by confidence and return top_k
    all_results.sort(key=lambda x: -x.confidence_score)
    return all_results[:top_k]


# ------------------------------------------------------------------------------
# Authorization Helpers
# ------------------------------------------------------------------------------

def get_accessible_corpora(user_name: str) -> List[DataSourceInfo]:
    """Get list of data sources accessible by a user."""
    user_groups = set(USERS_GROUPS_DB.get(user_name, []))
    accessible = []

    for corpus_id, corpus in DATA_SOURCES.items():
        required = set(corpus.get("required_groups", []))

        # Public if no required groups, or user has at least one required group
        if not required or (user_groups & required):
            accessible.append(DataSourceInfo(
                id=corpus_id,
                label=corpus.get("name", corpus_id),
                compliance_level=corpus.get("compliance_level", "CUI"),
                description=corpus.get("description", ""),
            ))

    return accessible


def can_access_corpus(user_name: str, corpus_id: str) -> bool:
    """Check if a user can access a corpus."""
    if corpus_id not in DATA_SOURCES:
        return False

    required = set(DATA_SOURCES[corpus_id].get("required_groups", []))
    if not required:
        return True  # Public access

    user_groups = set(USERS_GROUPS_DB.get(user_name, []))
    return bool(user_groups & required)


# ------------------------------------------------------------------------------
# API Endpoints
# ------------------------------------------------------------------------------

@app.get("/discover/datasources", response_model=DataSourceDiscoveryResponse)
async def discover_data_sources(as_user: str = Query(...)):
    """Discover data sources accessible by a user."""
    logger.info("Discovery request for user: %s", as_user)

    accessible = get_accessible_corpora(as_user)
    logger.info("User %s can access %d data sources", as_user, len(accessible))

    return DataSourceDiscoveryResponse(
        data_sources=accessible,
    )


@app.post("/rag/completions", response_model=RagResponse)
async def rag_completions(request: RagRequest, as_user: str = Query(...)):
    """Query RAG with grep-based search."""
    start_time = time.time()

    logger.info("---------- RAG query ----------")
    logger.info("RAG query from user: %s, corpora: %s", as_user, request.corpora)

    # Determine corpora to search
    corpora_to_search = request.corpora or [c.id for c in get_accessible_corpora(as_user)]

    # Validate access
    for corpus in corpora_to_search:
        if corpus not in DATA_SOURCES:
            raise HTTPException(status_code=404, detail=f"Corpus '{corpus}' not found")
        if not can_access_corpus(as_user, corpus):
            raise HTTPException(status_code=403, detail=f"Access denied to '{corpus}'")

    # Extract user query
    user_query = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    logger.info("RAG user query (exact): %r", user_query)

    # Search each corpus
    all_documents = []
    for corpus in corpora_to_search:
        docs = search_corpus(user_query, corpus, request.top_k)
        all_documents.extend(docs)

    # Sort by confidence and limit
    all_documents.sort(key=lambda x: -x.confidence_score)
    all_documents = all_documents[:request.top_k]

    # Generate response
    processing_time = int((time.time() - start_time) * 1000) + 20

    if all_documents:
        context_parts = [f"[{d.title}]\n{d.text}" for d in all_documents]
        context = "\n\n---\n\n".join(context_parts)

        response_content = (
            f"Based on searching {len(corpora_to_search)} data source(s), "
            f"I found {len(all_documents)} relevant result(s):\n\n"
            f"{context}\n\n"
            f"These results are from: {', '.join(set(d.corpus_id for d in all_documents))}"
        )
    else:
        response_content = (
            f"No results found for: \"{user_query}\"\n\n"
            f"Searched in: {', '.join(corpora_to_search)}\n"
            "Try different keywords or check your data source access."
        )

    return RagResponse(
        model=request.model,
        choices=[RagResponseChoice(message=ChatMessage(role="assistant", content=response_content))],
        rag_metadata=RagMetadata(
            query_processing_time_ms=processing_time,
            documents_found=all_documents,
            data_sources=corpora_to_search,
            retrieval_method="keyword-search",
        ),
    )


@app.get("/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/")
async def root():
    return {
        "service": "ATLAS RAG API Mock",
        "version": "2.0.0",
        "search_method": "grep-based keyword search",
        "data_sources": list(DATA_SOURCES.keys()),
        "test_users": list(USERS_GROUPS_DB.keys()),
        "endpoints": {
            "GET /discover/datasources?as_user=<email>": "List accessible data sources",
            "POST /rag/completions?as_user=<email>": "Search and query",
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
