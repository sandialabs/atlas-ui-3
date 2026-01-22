#!/usr/bin/env python3
"""
ATLAS RAG API Mock Service with Grep-Based Search

Provides mock endpoints that simulate the external ATLAS RAG API:
  - GET  /discover/datasources  - Discover accessible data sources
  - POST /rag/completions       - Query RAG with grep-based search

This mock searches through realistic text data using simple keyword matching.
"""

import logging
import os
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
# Realistic Data Sources with Searchable Content
# ------------------------------------------------------------------------------

# Each document is a dict with id, title, content, and metadata
DATA_SOURCES = {
    "company-policies": {
        "name": "Company Policies",
        "compliance_level": "Internal",
        "required_groups": ["employee"],
        "documents": [
            {
                "id": "pol-001",
                "title": "Remote Work Policy",
                "content": """Remote Work Policy - Effective January 2024

1. ELIGIBILITY
All full-time employees who have completed their 90-day probationary period are eligible for remote work arrangements. Contractors and temporary staff must obtain manager approval.

2. WORK HOURS
Remote employees must maintain core hours of 10:00 AM to 3:00 PM in their local timezone. Employees must be available for meetings and collaboration during these hours. Flexible scheduling outside core hours is permitted with manager approval.

3. EQUIPMENT AND WORKSPACE
The company provides a laptop, monitor, and $500 home office stipend for ergonomic equipment. Employees are responsible for maintaining a dedicated workspace with reliable internet (minimum 50 Mbps). IT support is available 24/7 for technical issues.

4. SECURITY REQUIREMENTS
All work must be performed on company-issued devices. VPN connection is mandatory when accessing internal systems. Employees must not work from public WiFi networks without VPN. Sensitive documents must not be printed at home.

5. COMMUNICATION
Daily check-ins via Slack are expected. Video must be enabled for all team meetings. Response time for messages should be within 2 hours during work hours. Weekly 1:1 meetings with managers are mandatory.

6. PERFORMANCE EXPECTATIONS
Remote employees are held to the same performance standards as in-office staff. Productivity is measured by output and deliverables, not hours logged. Quarterly reviews will assess remote work effectiveness.""",
                "last_modified": "2024-01-15",
            },
            {
                "id": "pol-002",
                "title": "Expense Reimbursement Policy",
                "content": """Expense Reimbursement Policy

1. ELIGIBLE EXPENSES
Travel: Airfare (economy class), hotels ($200/night max), meals ($75/day), ground transportation.
Business meals: Client entertainment up to $150/person with prior approval.
Office supplies: Up to $50/month without approval, larger purchases need manager sign-off.
Professional development: Conferences, courses, and certifications up to $2,500/year.

2. SUBMISSION PROCESS
All expenses must be submitted within 30 days of incurrence via the Expensify app. Original receipts are required for expenses over $25. Manager approval is needed for expenses over $500. Finance reviews and processes approved expenses within 5 business days.

3. TRAVEL BOOKING
Use the corporate travel portal (Concur) for all travel bookings. Book flights at least 14 days in advance when possible. Preferred hotel chains: Marriott, Hilton, Hyatt. Rental cars require VP approval unless public transit is unavailable.

4. NON-REIMBURSABLE EXPENSES
Personal entertainment, alcohol (except client entertainment), gym memberships, commuting costs, personal phone bills, airline upgrades, hotel minibar charges.

5. CORPORATE CARDS
Directors and above receive corporate American Express cards. Monthly statements must be reconciled within 10 days. Misuse of corporate cards may result in revocation and disciplinary action.""",
                "last_modified": "2024-02-01",
            },
            {
                "id": "pol-003",
                "title": "Code of Conduct",
                "content": """Employee Code of Conduct

CORE VALUES
Integrity: We act honestly and ethically in all business dealings.
Respect: We treat colleagues, customers, and partners with dignity.
Excellence: We strive for quality in everything we do.
Innovation: We embrace new ideas and continuous improvement.

WORKPLACE BEHAVIOR
Harassment and discrimination of any kind are strictly prohibited. This includes comments or actions based on race, gender, religion, age, disability, or sexual orientation. Report concerns to HR or use the anonymous ethics hotline.

CONFLICTS OF INTEREST
Employees must disclose any personal or financial interests that could conflict with company interests. Outside employment requires written approval. Gifts from vendors over $100 must be reported and may need to be declined.

CONFIDENTIALITY
Proprietary information must not be shared outside the company. NDAs must be signed before accessing sensitive projects. Customer data is subject to strict privacy regulations. Violations may result in termination and legal action.

DATA PROTECTION
Handle customer and employee data according to GDPR and CCPA requirements. Use strong passwords and enable two-factor authentication. Report any suspected data breaches immediately to the security team. Do not store sensitive data on personal devices.

SOCIAL MEDIA
Personal social media use should not reflect negatively on the company. Do not share confidential information online. Official company statements are made only by authorized spokespersons.

REPORTING VIOLATIONS
Use the ethics hotline: 1-800-555-ETHICS or ethics@company.com. Reports can be made anonymously. Retaliation against reporters is strictly prohibited and grounds for termination.""",
                "last_modified": "2024-01-01",
            },
            {
                "id": "pol-004",
                "title": "PTO and Leave Policy",
                "content": """Paid Time Off and Leave Policy

ANNUAL PTO ALLOCATION
Years 0-2: 15 days PTO
Years 3-5: 20 days PTO
Years 6+: 25 days PTO

PTO does not roll over to the next year. Unused PTO is not paid out except where required by state law. PTO requests should be submitted at least 2 weeks in advance for periods over 3 days.

SICK LEAVE
Employees receive 10 sick days per year. Sick leave can be used for personal illness, medical appointments, or caring for immediate family. A doctor's note is required for absences exceeding 3 consecutive days.

PARENTAL LEAVE
Primary caregivers: 16 weeks paid leave
Secondary caregivers: 6 weeks paid leave
Leave must be taken within 12 months of birth or adoption. Employees may request flexible return arrangements.

BEREAVEMENT
Immediate family (spouse, parent, child, sibling): 5 days
Extended family (grandparent, in-law): 3 days
Close friend: 1 day with manager approval

HOLIDAYS
The company observes 10 federal holidays plus 2 floating holidays. Floating holidays must be used within the calendar year. Holiday schedule is published annually in December.

JURY DUTY
Full pay is provided for jury duty. Provide summons to HR. Return to work on days when court is not in session.""",
                "last_modified": "2024-01-10",
            },
        ],
    },
    "technical-docs": {
        "name": "Technical Documentation",
        "compliance_level": "Internal",
        "required_groups": ["engineering", "devops"],
        "documents": [
            {
                "id": "tech-001",
                "title": "API Authentication Guide",
                "content": """API Authentication Guide

OVERVIEW
Our API uses OAuth 2.0 with JWT tokens for authentication. All API requests must include a valid access token in the Authorization header.

OBTAINING ACCESS TOKENS
1. Register your application in the Developer Portal to get client_id and client_secret
2. Exchange credentials for an access token:
   POST /oauth/token
   Content-Type: application/x-www-form-urlencoded

   grant_type=client_credentials
   client_id=your_client_id
   client_secret=your_client_secret

3. The response includes:
   {
     "access_token": "eyJhbGciOiJSUzI1NiIs...",
     "token_type": "Bearer",
     "expires_in": 3600
   }

USING ACCESS TOKENS
Include the token in all API requests:
Authorization: Bearer eyJhbGciOiJSUzI1NiIs...

Tokens expire after 1 hour. Implement token refresh before expiration to avoid service interruption.

TOKEN REFRESH
POST /oauth/token
grant_type=refresh_token
refresh_token=your_refresh_token

SCOPES
read:users - Read user profiles
write:users - Modify user profiles
read:data - Read application data
write:data - Write application data
admin - Full administrative access

Request only the scopes your application needs. Excessive scope requests will be rejected.

ERROR HANDLING
401 Unauthorized: Token is invalid or expired
403 Forbidden: Token lacks required scope
429 Too Many Requests: Rate limit exceeded (100 requests/minute)

SECURITY BEST PRACTICES
Store tokens securely, never in client-side code
Use HTTPS for all API calls
Rotate client secrets every 90 days
Implement token revocation for compromised credentials""",
                "last_modified": "2024-03-01",
            },
            {
                "id": "tech-002",
                "title": "Database Schema Documentation",
                "content": """Database Schema Documentation

USERS TABLE
Primary table for user accounts.

CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    role ENUM('admin', 'user', 'viewer') DEFAULT 'user',
    status ENUM('active', 'inactive', 'suspended') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    mfa_enabled BOOLEAN DEFAULT FALSE
);

Indexes:
- idx_users_email ON users(email)
- idx_users_status ON users(status)
- idx_users_created ON users(created_at)

PROJECTS TABLE
Stores project information.

CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    owner_id UUID REFERENCES users(id),
    status ENUM('active', 'archived', 'deleted') DEFAULT 'active',
    visibility ENUM('public', 'private', 'team') DEFAULT 'private',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

AUDIT_LOGS TABLE
Tracks all system changes for compliance.

CREATE TABLE audit_logs (
    id BIGSERIAL PRIMARY KEY,
    user_id UUID REFERENCES users(id),
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id UUID,
    old_values JSONB,
    new_values JSONB,
    ip_address INET,
    user_agent TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

Partition by month for performance:
CREATE TABLE audit_logs_2024_01 PARTITION OF audit_logs
FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

PERFORMANCE NOTES
- Use connection pooling (PgBouncer recommended, max 100 connections)
- Enable query logging for slow queries (> 100ms)
- Run VACUUM ANALYZE weekly on large tables
- Archive audit_logs older than 2 years to cold storage""",
                "last_modified": "2024-02-15",
            },
            {
                "id": "tech-003",
                "title": "Deployment Pipeline",
                "content": """Deployment Pipeline Documentation

ENVIRONMENTS
1. Development (dev) - For active development and testing
2. Staging (stg) - Pre-production validation
3. Production (prod) - Live customer-facing environment

PIPELINE STAGES

Stage 1: Build
- Triggered on push to main branch or PR creation
- Runs linting (ESLint, Pylint) and code formatting checks
- Builds Docker images with commit SHA tags
- Runs unit tests (minimum 80% coverage required)
- Publishes images to private registry (gcr.io/company-prod)

Stage 2: Security Scan
- Container scanning with Trivy for vulnerabilities
- SAST with SonarQube (no critical/high issues allowed)
- Dependency check for known CVEs
- Secrets scanning to prevent credential leaks

Stage 3: Deploy to Staging
- Automatic deployment after build success
- Blue-green deployment strategy
- Runs integration tests against staging APIs
- Performance tests (response time < 200ms at P95)
- Smoke tests for critical user journeys

Stage 4: Production Deployment
- Requires manual approval from tech lead
- Canary deployment: 10% traffic for 30 minutes
- Automatic rollback if error rate exceeds 1%
- Full rollout after canary validation
- Post-deployment verification tests

ROLLBACK PROCEDURES
Automatic rollback triggers:
- Error rate > 1% for 5 minutes
- P95 latency > 500ms
- Health check failures

Manual rollback:
kubectl rollout undo deployment/app-name -n production

MONITORING
- Datadog for metrics and APM
- PagerDuty for alerting
- Deployment notifications in #deployments Slack channel

HOTFIX PROCESS
1. Create hotfix branch from production tag
2. Apply minimal fix with tests
3. Fast-track approval (on-call engineer + manager)
4. Direct deploy to production, backport to main""",
                "last_modified": "2024-03-10",
            },
            {
                "id": "tech-004",
                "title": "Microservices Architecture",
                "content": """Microservices Architecture Overview

SERVICE CATALOG

1. API Gateway (api-gateway)
   - Routes external requests to internal services
   - Handles authentication and rate limiting
   - Technology: Kong on Kubernetes
   - Port: 443 (external), 8000 (internal)

2. User Service (user-service)
   - Manages user accounts, profiles, and authentication
   - Owns the users database
   - Technology: Python/FastAPI
   - Dependencies: PostgreSQL, Redis

3. Project Service (project-service)
   - Handles project CRUD operations
   - Manages project memberships and permissions
   - Technology: Node.js/Express
   - Dependencies: PostgreSQL, Elasticsearch

4. Notification Service (notification-service)
   - Sends emails, SMS, and push notifications
   - Queue-based for reliability
   - Technology: Go
   - Dependencies: RabbitMQ, SendGrid, Twilio

5. Analytics Service (analytics-service)
   - Collects and processes usage metrics
   - Generates reports and dashboards
   - Technology: Python/Flask
   - Dependencies: ClickHouse, Kafka

COMMUNICATION PATTERNS
- Synchronous: REST APIs for client-facing operations
- Asynchronous: RabbitMQ for background jobs
- Event streaming: Kafka for real-time data pipeline

SERVICE MESH
Using Istio for:
- Mutual TLS between services
- Traffic management and load balancing
- Observability and distributed tracing
- Circuit breaking for fault tolerance

SCALING POLICIES
- Horizontal Pod Autoscaler based on CPU (target 70%)
- Custom metrics autoscaling for queue depth
- Minimum replicas: 2 (prod), 1 (staging)
- Maximum replicas: 20 (prod), 5 (staging)

HEALTH CHECKS
All services must implement:
GET /health - Basic liveness check
GET /ready - Readiness with dependency checks
Response time must be < 100ms for health endpoints""",
                "last_modified": "2024-02-28",
            },
        ],
    },
    "product-knowledge": {
        "name": "Product Knowledge Base",
        "compliance_level": "Public",
        "required_groups": [],  # Public access
        "documents": [
            {
                "id": "prod-001",
                "title": "Getting Started Guide",
                "content": """Getting Started with DataFlow Pro

WELCOME
DataFlow Pro is an enterprise data integration platform that connects your applications, databases, and cloud services. This guide will help you set up your first data pipeline in under 30 minutes.

SYSTEM REQUIREMENTS
- Modern web browser (Chrome, Firefox, Safari, Edge)
- Network access to source and destination systems
- API credentials for connected services

STEP 1: CREATE YOUR ACCOUNT
Visit app.dataflowpro.com and click "Start Free Trial". Enter your business email and create a password. Verify your email within 24 hours to activate your account.

STEP 2: CONNECT YOUR FIRST SOURCE
Navigate to Connections > Add New. Select your data source type (Database, API, File Storage, or SaaS App). Enter the connection details and credentials. Click "Test Connection" to verify access.

Supported sources include:
- Databases: PostgreSQL, MySQL, SQL Server, Oracle, MongoDB
- Cloud Storage: AWS S3, Google Cloud Storage, Azure Blob
- SaaS Apps: Salesforce, HubSpot, Zendesk, Shopify
- APIs: Any REST or GraphQL endpoint

STEP 3: CREATE A PIPELINE
Click "New Pipeline" from the dashboard. Select your source and destination connections. Choose sync frequency: real-time, hourly, daily, or custom. Map fields between source and destination. Enable the pipeline to start syncing.

STEP 4: MONITOR YOUR DATA
View sync status on the Pipeline Dashboard. Check data quality metrics and error logs. Set up alerts for sync failures or data anomalies.

NEED HELP?
Documentation: docs.dataflowpro.com
Support: support@dataflowpro.com
Community: community.dataflowpro.com""",
                "last_modified": "2024-03-15",
            },
            {
                "id": "prod-002",
                "title": "Troubleshooting Common Issues",
                "content": """Troubleshooting Guide

CONNECTION ERRORS

Problem: "Connection refused" error
Causes:
- Firewall blocking outbound connections
- Incorrect hostname or port
- Service not running on destination

Solutions:
1. Whitelist DataFlow Pro IPs: 52.1.2.3, 52.1.2.4, 52.1.2.5
2. Verify hostname resolves correctly: nslookup hostname
3. Check service status on destination server
4. Try connecting from a different network

Problem: "Authentication failed" error
Causes:
- Invalid credentials
- Expired API key or token
- Insufficient permissions

Solutions:
1. Regenerate API credentials in the source system
2. Verify the user has read access to required tables
3. Check for password special characters (escape if needed)
4. Ensure OAuth token hasn't expired

SYNC ISSUES

Problem: Sync is slow or timing out
Causes:
- Large data volume without pagination
- Network latency
- Source system under heavy load

Solutions:
1. Enable incremental sync instead of full refresh
2. Add filters to reduce data volume
3. Schedule syncs during off-peak hours
4. Increase timeout in pipeline settings

Problem: Missing or duplicate data
Causes:
- Primary key not configured correctly
- Sync interrupted mid-process
- Schema changes in source

Solutions:
1. Verify primary key column is set in pipeline config
2. Enable "upsert" mode for idempotent syncs
3. Run a full refresh to resync all data
4. Check field mappings after schema changes

PERFORMANCE OPTIMIZATION

For large datasets (>1M rows):
- Use change data capture (CDC) when available
- Partition data by date or region
- Enable parallel processing (up to 4 threads)
- Consider staging to intermediate storage

Contact support@dataflowpro.com for enterprise performance tuning.""",
                "last_modified": "2024-03-12",
            },
            {
                "id": "prod-003",
                "title": "Feature Comparison by Plan",
                "content": """DataFlow Pro Plans and Features

STARTER PLAN - $49/month
Best for small teams and simple integrations

Included:
- 5 active pipelines
- 100,000 rows synced per month
- 10 pre-built connectors
- Daily sync frequency
- Email support (48-hour response)
- 7-day data retention

Limitations:
- No custom connectors
- No real-time sync
- Single user only

PROFESSIONAL PLAN - $199/month
Best for growing teams with complex data needs

Everything in Starter, plus:
- 25 active pipelines
- 1,000,000 rows synced per month
- 50+ pre-built connectors
- Hourly sync frequency
- Priority email support (24-hour response)
- 30-day data retention
- Up to 5 team members
- Custom field transformations
- Slack notifications
- API access

ENTERPRISE PLAN - Custom pricing
Best for large organizations with advanced requirements

Everything in Professional, plus:
- Unlimited pipelines
- Unlimited row syncs
- All connectors including custom
- Real-time sync (CDC)
- Dedicated support engineer
- 1-year data retention
- Unlimited team members
- SSO/SAML integration
- Custom SLA (99.9% uptime)
- On-premise deployment option
- SOC 2 Type II compliance
- HIPAA compliance (healthcare)
- Dedicated infrastructure

ADD-ONS (Available on any plan)
- Additional connectors: $25/month each
- Extended retention: $50/month per year
- Priority support upgrade: $100/month
- Custom connector development: Starting at $2,500

Contact sales@dataflowpro.com for Enterprise pricing.""",
                "last_modified": "2024-03-01",
            },
            {
                "id": "prod-004",
                "title": "API Reference Overview",
                "content": """DataFlow Pro API Reference

BASE URL
https://api.dataflowpro.com/v1

AUTHENTICATION
All API requests require a Bearer token in the Authorization header.
Generate API keys in Settings > API Keys.

Authorization: Bearer dfp_live_abc123xyz

RATE LIMITS
- Standard plans: 100 requests/minute
- Enterprise plans: 1000 requests/minute
- Burst allowance: 2x limit for 10 seconds

ENDPOINTS

GET /pipelines
List all pipelines in your account.
Query params: status, limit, offset

POST /pipelines
Create a new pipeline.
Required fields: name, source_id, destination_id, schedule

GET /pipelines/{id}
Get pipeline details including configuration and stats.

PUT /pipelines/{id}
Update pipeline configuration.

DELETE /pipelines/{id}
Delete a pipeline. This action cannot be undone.

POST /pipelines/{id}/run
Trigger an immediate sync for the pipeline.

GET /pipelines/{id}/runs
List recent sync runs with status and metrics.

GET /connections
List all configured connections.

POST /connections
Create a new connection.
Required fields: name, type, credentials

POST /connections/{id}/test
Test connection connectivity.

WEBHOOKS
Configure webhooks to receive real-time notifications:
- pipeline.sync.started
- pipeline.sync.completed
- pipeline.sync.failed
- pipeline.error.threshold

Webhook payloads are signed with HMAC-SHA256.
Verify using the X-DataFlow-Signature header.

SDK LIBRARIES
Python: pip install dataflowpro
Node.js: npm install @dataflowpro/sdk
Ruby: gem install dataflowpro
Go: go get github.com/dataflowpro/go-sdk

Full API documentation: docs.dataflowpro.com/api""",
                "last_modified": "2024-03-08",
            },
        ],
    },
}

# User permissions (which groups each user belongs to)
USERS_GROUPS_DB = {
    "alice@example.com": ["employee", "engineering"],
    "bob@example.com": ["employee", "sales"],
    "charlie@example.com": ["employee", "engineering", "devops"],
    "test@test.com": ["employee", "engineering", "devops", "admin"],
    "guest@example.com": [],  # No groups, only public access
}


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
    name: str
    compliance_level: str = "Internal"


class DataSourceDiscoveryResponse(BaseModel):
    user_name: str
    accessible_data_sources: List[DataSourceInfo]


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
    text_lower = text.lower()
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
                name=corpus_id,
                compliance_level=corpus.get("compliance_level", "Internal"),
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
        user_name=as_user,
        accessible_data_sources=accessible,
    )


@app.post("/rag/completions", response_model=RagResponse)
async def rag_completions(request: RagRequest, as_user: str = Query(...)):
    """Query RAG with grep-based search."""
    start_time = time.time()

    logger.info("RAG query from user: %s, corpora: %s", as_user, request.corpora)

    # Determine corpora to search
    corpora_to_search = request.corpora or [c.name for c in get_accessible_corpora(as_user)]

    # Validate access
    for corpus in corpora_to_search:
        if corpus not in DATA_SOURCES:
            raise HTTPException(status_code=404, detail=f"Corpus '{corpus}' not found")
        if not can_access_corpus(as_user, corpus):
            raise HTTPException(status_code=403, detail=f"Access denied to '{corpus}'")

    # Extract user query
    user_query = next((m.content for m in reversed(request.messages) if m.role == "user"), "")

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
