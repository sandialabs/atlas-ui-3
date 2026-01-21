#!/usr/bin/env python3
"""
External ATLAS RAG API Mock Service

Provides mock endpoints that simulate the external ATLAS RAG API:
  - GET  /discover/datasources  - Discover accessible data sources (with as_user param)
  - POST /rag/completions       - Query RAG for completions (with as_user param)

This mock is designed to test the ExternalRAGClient integration without
requiring access to the real ATLAS RAG API.

API Format:
- Authentication: Bearer token in Authorization header
- User impersonation: as_user query parameter
- Response format: OpenAI ChatCompletion with rag_metadata extension
"""

import logging
import time
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Header, Query
from pydantic import BaseModel, Field
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Initialize FastAPI App
# ------------------------------------------------------------------------------

app = FastAPI(
    title="External ATLAS RAG API Mock",
    description="Mock API that simulates the external ATLAS RAG API for testing",
    version="1.0.0",
)

# ------------------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------------------

# Expected bearer token for authentication (None means no auth required)
EXPECTED_BEARER_TOKEN = "test-atlas-rag-token"

# ------------------------------------------------------------------------------
# Mock Data Structures
# ------------------------------------------------------------------------------

# Mock database of users and the groups they belong to
USERS_GROUPS_DB = {
    "alice@example.com": ["engineering", "data-science"],
    "bob@example.com": ["sales", "marketing"],
    "charlie@example.com": ["engineering", "devops"],
    "diana@example.com": ["finance", "executive"],
    "test@test.com": ["engineering", "finance", "admin"],
    "guest@example.com": ["public"],
}

# Mock database mapping corpora to required groups (empty = public)
CORPORA_PERMISSIONS_DB = {
    "engineering-docs": ["engineering"],
    "sales-playbook": ["sales", "marketing"],
    "kubernetes-runbooks": ["engineering", "devops"],
    "financial-reports": ["finance", "executive"],
    "company-wiki": [],  # Public - anyone can access
    "research-papers": ["data-science", "engineering"],
}

# Compliance levels for each corpus
CORPORA_COMPLIANCE_DB = {
    "engineering-docs": "Internal",
    "sales-playbook": "Internal",
    "kubernetes-runbooks": "CUI",
    "financial-reports": "CUI",
    "company-wiki": "Public",
    "research-papers": "Internal",
}

# Mock RAG content for each corpus
RAG_CONTENT_DB = {
    "engineering-docs": """
The engineering documentation covers our microservices architecture.
Key components include:
- API Gateway: Handles authentication and request routing
- User Service: Manages user accounts and permissions
- Data Pipeline: Processes and transforms incoming data
All services communicate via gRPC with REST fallback.
""".strip(),
    "sales-playbook": """
Q4 Sales Strategy focuses on enterprise expansion.
Target verticals: Healthcare, Finance, Government
Key differentiators:
- 99.99% uptime SLA
- SOC2 Type II compliance
- On-premise deployment option
Contact sales-ops for pricing tiers.
""".strip(),
    "kubernetes-runbooks": """
Kubernetes Cluster Operations:
1. Scaling: Use HPA for automatic pod scaling
2. Monitoring: Prometheus + Grafana dashboards
3. Incidents: Follow PagerDuty escalation policy
4. Deployments: ArgoCD GitOps workflow
Critical: Always check resource quotas before scaling.
""".strip(),
    "financial-reports": """
Q3 Financial Summary:
- Revenue: $12.5M (up 23% YoY)
- Operating Expenses: $8.2M
- Net Income: $4.3M
Key investments: R&D expansion, new data center
Forecast: Q4 projected at $15M revenue.
""".strip(),
    "company-wiki": """
Company Overview:
Founded in 2020, we build enterprise AI solutions.
Mission: Make AI accessible and secure for businesses.
Values: Innovation, Integrity, Customer Success
Office locations: San Francisco, New York, London
""".strip(),
    "research-papers": """
Latest Research: Retrieval-Augmented Generation (RAG)
Key findings:
- Hybrid search (BM25 + dense) outperforms single methods
- Chunk size of 512 tokens optimal for most use cases
- Re-ranking with cross-encoders improves relevance by 15%
Future work: Multi-modal retrieval with vision embeddings.
""".strip(),
}

# Mock document metadata for search results
MOCK_DOCUMENTS_DB = {
    "engineering-docs": [
        {
            "id": "eng-001",
            "corpus_id": "engineering-docs",
            "text": "API Gateway handles authentication...",
            "confidence_score": 0.95,
            "content_type": "markdown",
            "last_modified": "2025-12-15T10:30:00Z",
        },
        {
            "id": "eng-002",
            "corpus_id": "engineering-docs",
            "text": "Microservices communicate via gRPC...",
            "confidence_score": 0.88,
            "content_type": "markdown",
            "last_modified": "2025-12-10T14:20:00Z",
        },
    ],
    "sales-playbook": [
        {
            "id": "sales-001",
            "corpus_id": "sales-playbook",
            "text": "Enterprise expansion strategy...",
            "confidence_score": 0.92,
            "content_type": "document",
            "last_modified": "2025-11-20T09:00:00Z",
        },
    ],
    "kubernetes-runbooks": [
        {
            "id": "k8s-001",
            "corpus_id": "kubernetes-runbooks",
            "text": "HPA automatic scaling configuration...",
            "confidence_score": 0.97,
            "content_type": "yaml",
            "last_modified": "2025-12-28T16:45:00Z",
        },
        {
            "id": "k8s-002",
            "corpus_id": "kubernetes-runbooks",
            "text": "PagerDuty escalation procedures...",
            "confidence_score": 0.85,
            "content_type": "markdown",
            "last_modified": "2025-12-25T11:30:00Z",
        },
    ],
    "financial-reports": [
        {
            "id": "fin-001",
            "corpus_id": "financial-reports",
            "text": "Q3 revenue analysis...",
            "confidence_score": 0.94,
            "content_type": "spreadsheet",
            "last_modified": "2025-10-01T08:00:00Z",
        },
    ],
    "company-wiki": [
        {
            "id": "wiki-001",
            "corpus_id": "company-wiki",
            "text": "Company mission and values...",
            "confidence_score": 0.89,
            "content_type": "wiki",
            "last_modified": "2025-06-15T12:00:00Z",
        },
    ],
    "research-papers": [
        {
            "id": "research-001",
            "corpus_id": "research-papers",
            "text": "RAG optimization techniques...",
            "confidence_score": 0.96,
            "content_type": "pdf",
            "last_modified": "2025-12-01T09:30:00Z",
        },
    ],
}


# ------------------------------------------------------------------------------
# Pydantic Models
# ------------------------------------------------------------------------------

class DataSourceInfo(BaseModel):
    """Information about a data source."""
    name: str = Field(..., description="Name of the data source/corpus")
    compliance_level: str = Field(
        default="CUI", description="Compliance level of the data source"
    )


class DataSourceDiscoveryResponse(BaseModel):
    """Response for data source discovery."""
    user_name: str = Field(..., description="The user who made the request")
    accessible_data_sources: List[DataSourceInfo] = Field(
        default_factory=list,
        description="List of data sources the user can access",
    )


class ChatMessage(BaseModel):
    """A chat message."""
    role: str = Field(..., description="Role: user, assistant, or system")
    content: str = Field(..., description="Message content")


class RagRequest(BaseModel):
    """Request body for RAG completions."""
    messages: List[ChatMessage] = Field(..., description="Conversation messages")
    stream: bool = Field(default=False, description="Whether to stream response")
    model: str = Field(
        default="openai/gpt-oss-120b", description="Model to use for generation"
    )
    top_k: int = Field(default=4, description="Number of documents to retrieve")
    corpora: Optional[List[str]] = Field(
        None, description="List of corpora to search"
    )
    threshold: Optional[float] = Field(
        None, description="Minimum relevance threshold"
    )
    expanded_window: Optional[List[int]] = Field(
        default=[0, 0], description="Context window expansion"
    )


class DocumentFound(BaseModel):
    """Metadata about a retrieved document."""
    id: Optional[str] = Field(None, description="Document ID")
    corpus_id: str = Field(..., description="Source corpus ID")
    text: str = Field(..., description="Document text snippet")
    confidence_score: float = Field(..., description="Relevance confidence score")
    content_type: str = Field(default="atlas-search", description="Content type")
    last_modified: Optional[str] = Field(None, description="Last modified timestamp")


class RagMetadata(BaseModel):
    """Metadata about RAG processing."""
    query_processing_time_ms: int = Field(..., description="Processing time in ms")
    documents_found: List[DocumentFound] = Field(
        default_factory=list, description="Retrieved documents"
    )
    data_sources: List[str] = Field(
        default_factory=list, description="Data sources queried"
    )
    retrieval_method: str = Field(
        default="similarity", description="Retrieval method used"
    )


class RagResponseChoice(BaseModel):
    """A choice in the RAG response."""
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class RagResponse(BaseModel):
    """OpenAI ChatCompletion-compatible response with RAG metadata."""
    id: str = Field(default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}")
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str = "openai/gpt-oss-120b"
    choices: List[RagResponseChoice]
    rag_metadata: Optional[RagMetadata] = None


# ------------------------------------------------------------------------------
# Authorization Helpers
# ------------------------------------------------------------------------------

def verify_bearer_token(authorization: Optional[str] = Header(None)) -> bool:
    """Verify the Bearer token in the Authorization header."""
    if EXPECTED_BEARER_TOKEN is None:
        return True

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Expected 'Bearer <token>'",
        )

    token = authorization[7:]  # Remove "Bearer " prefix
    if token != EXPECTED_BEARER_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Invalid bearer token",
        )

    return True


def get_user_groups(user_name: str) -> List[str]:
    """Get the groups a user belongs to."""
    return USERS_GROUPS_DB.get(user_name, [])


def can_access_corpus(user_name: str, corpus: str) -> bool:
    """Check if a user can access a corpus."""
    if corpus not in CORPORA_PERMISSIONS_DB:
        return False

    required_groups = CORPORA_PERMISSIONS_DB[corpus]

    # Public corpus - anyone can access
    if not required_groups:
        return True

    # Check if user is in any required group
    user_groups = get_user_groups(user_name)
    return any(group in user_groups for group in required_groups)


def get_accessible_corpora(user_name: str) -> List[DataSourceInfo]:
    """Get list of corpora accessible by a user."""
    accessible = []

    for corpus, required_groups in CORPORA_PERMISSIONS_DB.items():
        # Public corpus
        if not required_groups:
            compliance = CORPORA_COMPLIANCE_DB.get(corpus, "CUI")
            accessible.append(DataSourceInfo(name=corpus, compliance_level=compliance))
            continue

        # Check user groups
        user_groups = get_user_groups(user_name)
        if any(group in user_groups for group in required_groups):
            compliance = CORPORA_COMPLIANCE_DB.get(corpus, "CUI")
            accessible.append(DataSourceInfo(name=corpus, compliance_level=compliance))

    return accessible


# ------------------------------------------------------------------------------
# API Endpoints
# ------------------------------------------------------------------------------

@app.get("/discover/datasources", response_model=DataSourceDiscoveryResponse)
async def discover_data_sources(
    as_user: str = Query(..., description="User to discover data sources for"),
    authorization: Optional[str] = Header(None),
):
    """
    Discover data sources accessible by a user.

    Authentication: Bearer token in Authorization header
    User impersonation: as_user query parameter

    Returns list of data sources with name and compliance_level.
    """
    verify_bearer_token(authorization)

    logger.info(f"Discovery request for user: {as_user}")

    # Check if user exists
    if as_user not in USERS_GROUPS_DB:
        logger.warning(f"Unknown user: {as_user}")
        # Return empty list for unknown users (graceful degradation)
        return DataSourceDiscoveryResponse(
            user_name=as_user,
            accessible_data_sources=[],
        )

    accessible = get_accessible_corpora(as_user)

    logger.info(f"User {as_user} can access {len(accessible)} data sources")

    return DataSourceDiscoveryResponse(
        user_name=as_user,
        accessible_data_sources=accessible,
    )


@app.post("/rag/completions", response_model=RagResponse)
async def rag_completions(
    request: RagRequest,
    as_user: str = Query(..., description="User making the request"),
    authorization: Optional[str] = Header(None),
):
    """
    Query RAG for completions.

    Authentication: Bearer token in Authorization header
    User impersonation: as_user query parameter

    Returns OpenAI ChatCompletion format with rag_metadata extension.
    """
    start_time = time.time()

    verify_bearer_token(authorization)

    logger.info(
        f"RAG query from user: {as_user}, corpora: {request.corpora}, "
        f"model: {request.model}"
    )

    # Validate user exists
    if as_user not in USERS_GROUPS_DB:
        raise HTTPException(
            status_code=403,
            detail=f"User '{as_user}' not found or has no permissions",
        )

    # Determine which corpora to search
    corpora_to_search = request.corpora or []
    if not corpora_to_search:
        # If no corpora specified, search all accessible ones
        corpora_to_search = [c.name for c in get_accessible_corpora(as_user)]

    # Validate access to requested corpora
    documents_found = []
    content_parts = []

    for corpus in corpora_to_search:
        if corpus not in CORPORA_PERMISSIONS_DB:
            raise HTTPException(
                status_code=404,
                detail=f"Corpus '{corpus}' not found",
            )

        if not can_access_corpus(as_user, corpus):
            raise HTTPException(
                status_code=403,
                detail=f"User '{as_user}' does not have access to corpus '{corpus}'",
            )

        # Get mock documents for this corpus
        corpus_docs = MOCK_DOCUMENTS_DB.get(corpus, [])
        for doc in corpus_docs[:request.top_k]:
            documents_found.append(
                DocumentFound(
                    id=doc.get("id"),
                    corpus_id=doc.get("corpus_id", corpus),
                    text=doc.get("text", ""),
                    confidence_score=doc.get("confidence_score", 0.8),
                    content_type=doc.get("content_type", "atlas-search"),
                    last_modified=doc.get("last_modified"),
                )
            )

        # Get mock content
        corpus_content = RAG_CONTENT_DB.get(corpus)
        if corpus_content:
            content_parts.append(f"[From {corpus}]\n{corpus_content}")

    # Extract user query from messages
    user_query = "No query provided"
    for msg in reversed(request.messages):
        if msg.role == "user":
            user_query = msg.content
            break

    # Generate mock response
    if content_parts:
        retrieved_context = "\n\n".join(content_parts)
        response_content = (
            f"Based on the retrieved information from {len(corpora_to_search)} "
            f"data source(s), here is the answer to your query:\n\n"
            f"Query: {user_query}\n\n"
            f"Retrieved Context:\n{retrieved_context}\n\n"
            f"This response was generated from {len(documents_found)} relevant documents."
        )
    else:
        response_content = (
            f"No relevant information found for your query: {user_query}\n"
            "Please try a different search or check your data source access."
        )

    # Calculate processing time
    processing_time_ms = int((time.time() - start_time) * 1000) + 50  # Add base latency

    # Build response
    rag_metadata = RagMetadata(
        query_processing_time_ms=processing_time_ms,
        documents_found=documents_found,
        data_sources=corpora_to_search,
        retrieval_method="similarity",
    )

    response = RagResponse(
        model=request.model,
        choices=[
            RagResponseChoice(
                message=ChatMessage(role="assistant", content=response_content)
            )
        ],
        rag_metadata=rag_metadata,
    )

    logger.info(
        f"RAG query completed in {processing_time_ms}ms, "
        f"found {len(documents_found)} documents"
    )

    return response


# ------------------------------------------------------------------------------
# Utility Endpoints
# ------------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "service": "external-rag-mock",
    }


@app.get("/")
async def root():
    """Root endpoint with service information."""
    return {
        "service": "External ATLAS RAG API Mock",
        "version": "1.0.0",
        "description": "Mock API for testing ExternalRAGClient integration",
        "endpoints": {
            "GET /discover/datasources": "Discover accessible data sources",
            "POST /rag/completions": "Query RAG for completions",
            "GET /health": "Health check",
        },
        "authentication": {
            "type": "Bearer token",
            "header": "Authorization: Bearer <token>",
            "test_token": EXPECTED_BEARER_TOKEN,
        },
        "test_users": list(USERS_GROUPS_DB.keys()),
        "available_corpora": list(CORPORA_PERMISSIONS_DB.keys()),
    }


# ------------------------------------------------------------------------------
# Main Entry Point
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting External ATLAS RAG API Mock Service...")
    print()
    print("Available endpoints:")
    print("  - GET  /discover/datasources?as_user=<user>  - Discover data sources")
    print("  - POST /rag/completions?as_user=<user>       - Query RAG")
    print("  - GET  /health                               - Health check")
    print()
    print(f"Test bearer token: {EXPECTED_BEARER_TOKEN}")
    print(f"Test users: {', '.join(USERS_GROUPS_DB.keys())}")
    print()
    print("Default port: 8002")
    print()

    uvicorn.run(app, host="127.0.0.1", port=8002)
