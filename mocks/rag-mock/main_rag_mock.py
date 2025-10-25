# main.py
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, Body, Path
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

# ------------------------------------------------------------------------------
# 1. Initialize FastAPI App
# ------------------------------------------------------------------------------
app = FastAPI(
    title="RAG-style Secure Chat API",
    description="A mock API that mimics an OpenAI Chat Completion endpoint with data source authorization and includes a discovery endpoint.",
    version="1.1.0",
)

# ------------------------------------------------------------------------------
# 2. Mock Data Structures (Users, Groups, and Data Sources)
# ------------------------------------------------------------------------------

# Mock database of users and the groups they belong to.
# In a real system, this would come from an identity provider (e.g., Okta, Active Directory).
USERS_GROUPS_DB = {
    "alice": ["engineering", "infra-team"],
    "bob": ["sales", "business-dev"],
    "charlie": ["engineering", "product-team"],
    "diana": ["finance", "business-dev"],
    "eve": ["guest"], # A user with limited access
    "test@test.com": ["engineering", "finance"] # Added new test user
}

# Mock database mapping data sources to the groups that are allowed to access them.
# An empty list means the data source is public.
DATA_SOURCES_PERMISSIONS_DB = {
    "q3_sales_forecast": ["sales", "finance"],
    "production_db_schema": ["engineering"],
    "kubernetes_cluster_logs": ["infra-team"],
    "marketing_campaign_plan_q4": ["sales", "business-dev"],
    "public_company_handbook": [], # Accessible by everyone
}

# Mock RAG (Retrieval-Augmented Generation) data content.
# This is the actual data that gets "retrieved" based on the data_source.
RAG_DATA_DB = {
    "q3_sales_forecast": "Q3 Sales Forecast projects a 15% growth in the enterprise sector, pending new product launch.",
    "production_db_schema": "The 'users' table contains columns: id, username, email, created_at. The 'orders' table links to 'users' via user_id.",
    "kubernetes_cluster_logs": "Log entry from pod 'api-gateway-xyz123': INFO - Request processed successfully in 25ms. WARNING - High memory usage detected.",
    "marketing_campaign_plan_q4": "The Q4 marketing campaign 'Winter Wonders' will focus on social media engagement and influencer partnerships.",
    "public_company_handbook": "Our company values are integrity, innovation, and customer obsession. All employees are expected to complete annual security training.",
}

# Mock metadata for each data source's documents
RAG_METADATA_DB = {}

# ------------------------------------------------------------------------------
# 3. Authorization Logic
# ------------------------------------------------------------------------------

def is_user_in_group(user_name: str, group: str) -> bool:
    """
    Mock function to check if a user is in a specific group.
    This is a simplified stand-in for a real identity management check.
    """
    user_groups = USERS_GROUPS_DB.get(user_name, [])
    return group in user_groups

def authorize_user_for_data_source(user_name: str, data_source: str):
    """
    Dependency function to authorize a user for a given data source.
    It checks if the data source exists and if the user belongs to any of the
    required groups.
    """
    # Check 1: Does the requested data source exist?
    if data_source not in DATA_SOURCES_PERMISSIONS_DB:
        raise HTTPException(
            status_code=404,
            detail=f"Data source '{data_source}' not found."
        )

    required_groups = DATA_SOURCES_PERMISSIONS_DB[data_source]

    # Check 2: Is the data source public (no specific groups required)?
    if not required_groups:
        return True # Access granted for public data

    # Check 3: Does the user exist in our system?
    if user_name not in USERS_GROUPS_DB:
        raise HTTPException(
            status_code=403,
            detail=f"User '{user_name}' not found or has no permissions."
        )

    # Check 4: Is the user in at least one of the required groups?
    if not any(is_user_in_group(user_name, group) for group in required_groups):
        raise HTTPException(
            status_code=403,
            detail=f"User '{user_name}' is not authorized to access data source '{data_source}'. "
                   f"Requires one of the following groups: {required_groups}"
        )

    return True # Access granted

# ------------------------------------------------------------------------------
# 4. Pydantic Models for Request and Response (OpenAI-like)
# ------------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = Field(..., description="The role of the message author (e.g., 'user', 'system').")
    content: str = Field(..., description="The content of the message.")

class ChatCompletionRequest(BaseModel):
    messages: List[ChatMessage] = Field(..., description="A list of messages comprising the conversation so far.")
    user_name: str = Field(..., description="The username of the individual making the request.", json_schema_extra={"example": "alice"})
    data_source: str = Field(..., description="The specific data source to query for context.", json_schema_extra={"example": "kubernetes_cluster_logs"})
    model: str = "gpt-4-rag-mock" # Mock model name
    stream: bool = False # Mock streaming parameter

class DocumentMetadata(BaseModel):
    source: str = Field(..., description="The name/path of the source document")
    content_type: str = Field(..., description="Type of content (e.g., 'text', 'pdf', 'database')")
    confidence_score: float = Field(..., description="Relevance confidence score (0.0-1.0)")
    chunk_id: Optional[str] = Field(None, description="Identifier for the specific chunk/section")
    last_modified: Optional[str] = Field(None, description="Last modification timestamp")

class RAGMetadata(BaseModel):
    query_processing_time_ms: int = Field(..., description="Time taken to process the query in milliseconds")
    total_documents_searched: int = Field(..., description="Total number of documents searched")
    documents_found: List[DocumentMetadata] = Field(..., description="List of documents that were found and used")
    data_source_name: str = Field(..., description="Name of the data source queried")
    retrieval_method: str = Field(default="similarity_search", description="Method used for document retrieval")
    query_embedding_time_ms: Optional[int] = Field(None, description="Time taken for query embedding")

class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"

class ChatCompletionResponse(BaseModel):
    id: str = "chatcmpl-mock-12345"
    object: str = "chat.completion"
    created: int = 1677652288
    model: str = "gpt-4-rag-mock"
    choices: List[ChatCompletionChoice]
    rag_metadata: Optional[RAGMetadata] = Field(None, description="Metadata about the RAG processing")

class DataSourceDiscoveryResponse(BaseModel):
    user_name: str
    accessible_data_sources: List[str] = Field(..., description="A list of data source names the user can access.")


# ------------------------------------------------------------------------------
# 5. Initialize Mock Metadata (after model definitions)
# ------------------------------------------------------------------------------

# Initialize the metadata database with actual instances
RAG_METADATA_DB = {
    "q3_sales_forecast": [
        DocumentMetadata(
            source="sales_forecast_q3_2024.pdf",
            content_type="pdf",
            confidence_score=0.95,
            chunk_id="section_2",
            last_modified="2024-09-15T10:30:00Z"
        ),
        DocumentMetadata(
            source="enterprise_growth_analysis.xlsx",
            content_type="spreadsheet", 
            confidence_score=0.87,
            chunk_id="sheet_summary",
            last_modified="2024-09-10T14:20:00Z"
        )
    ],
    "production_db_schema": [
        DocumentMetadata(
            source="database_schema_documentation.md",
            content_type="text",
            confidence_score=0.98,
            chunk_id="users_table_section",
            last_modified="2024-08-30T09:15:00Z"
        )
    ],
    "kubernetes_cluster_logs": [
        DocumentMetadata(
            source="api-gateway-xyz123.log",
            content_type="log",
            confidence_score=0.92,
            chunk_id="recent_entries",
            last_modified="2024-12-29T15:45:00Z"
        ),
        DocumentMetadata(
            source="cluster_monitoring_dashboard.json",
            content_type="json",
            confidence_score=0.78,
            chunk_id="memory_metrics",
            last_modified="2024-12-29T15:40:00Z"
        )
    ],
    "marketing_campaign_plan_q4": [
        DocumentMetadata(
            source="q4_marketing_strategy.docx",
            content_type="document",
            confidence_score=0.94,
            chunk_id="winter_campaign",
            last_modified="2024-11-20T11:30:00Z"
        )
    ],
    "public_company_handbook": [
        DocumentMetadata(
            source="employee_handbook_2024.pdf",
            content_type="pdf",
            confidence_score=0.89,
            chunk_id="company_values",
            last_modified="2024-01-15T08:00:00Z"
        ),
        DocumentMetadata(
            source="security_training_requirements.md",
            content_type="text",
            confidence_score=0.85,
            chunk_id="annual_training",
            last_modified="2024-07-01T12:00:00Z"
        )
    ]
}


# ------------------------------------------------------------------------------
# 6. API Endpoints
# ------------------------------------------------------------------------------

@app.get(
    "/v1/discover/datasources/{user_name}",
    response_model=DataSourceDiscoveryResponse,
    summary="Discover data sources accessible by a user"
)
async def discover_data_sources(
    user_name: str = Path(..., description="The username to check permissions for.", json_schema_extra={"example": "test@test.com"})
):
    """
    Checks all available data sources and returns a list of the ones
    the specified user is authorized to access. This is useful for UIs
    that need to populate a dropdown of available data sources for a user.
    """
    print(f"Discovering data sources for user '{user_name}'")
    if user_name not in USERS_GROUPS_DB:
        raise HTTPException(status_code=404, detail=f"User '{user_name}' not found.")

    accessible_sources = []
    # Iterate over every data source and its required groups
    for source, required_groups in DATA_SOURCES_PERMISSIONS_DB.items():
        # If the data source is public, anyone can access it.
        if not required_groups:
            accessible_sources.append(source)
            continue

        # Check if the user is in any of the required groups for this data source.
        if any(is_user_in_group(user_name, group) for group in required_groups):
            accessible_sources.append(source)

    return DataSourceDiscoveryResponse(
        user_name=user_name,
        accessible_data_sources=accessible_sources
    )


@app.post(
    "/v1/chat/completions",
    response_model=ChatCompletionResponse,
    summary="Generate a chat completion with data source authorization"
)
async def create_chat_completion(request: ChatCompletionRequest):
    """
    This endpoint mimics the OpenAI Chat Completions API but adds two key fields:
    - `user_name`: To identify who is making the request.
    - `data_source`: To specify which internal knowledge base to use.

    The endpoint first checks if the `user_name` is authorized to access the
    `data_source`. If authorized, it retrieves the relevant data and constructs
    a mock response with detailed metadata about the RAG processing.
    """
    import time
    start_time = time.time()
    
    print(f"Received request from user '{request.user_name}' for data source '{request.data_source}'")
    
    # Authorize user for the data source
    authorize_user_for_data_source(request.user_name, request.data_source)
    
    retrieved_data = RAG_DATA_DB.get(request.data_source, "No specific data found, but access is permitted.")
    user_query = next((msg.content for msg in request.messages if msg.role == 'user'), "No user query found.")
    
    # Get metadata for this data source
    documents_found = RAG_METADATA_DB.get(request.data_source, [])
    
    # Calculate processing time
    processing_time_ms = int((time.time() - start_time) * 1000)
    
    # Create RAG metadata
    rag_metadata = RAGMetadata(
        query_processing_time_ms=processing_time_ms,
        total_documents_searched=len(documents_found) + 2,  # Simulate searching more than found
        documents_found=documents_found,
        data_source_name=request.data_source,
        retrieval_method="similarity_search",
        query_embedding_time_ms=processing_time_ms // 4  # Simulate embedding time
    )

    response_content = (
        f"Hello {request.user_name}. You have successfully accessed the '{request.data_source}' data source.\n\n"
        f"Based on your query ('{user_query}') and the retrieved context, here is the answer:\n\n"
        f"--- CONTEXT START ---\n"
        f"{retrieved_data}\n"
        f"--- CONTEXT END ---\n\n"
        f"This response was generated from {len(documents_found)} relevant documents."
    )

    response_message = ChatMessage(role="assistant", content=response_content)
    choice = ChatCompletionChoice(message=response_message)
    return ChatCompletionResponse(choices=[choice], rag_metadata=rag_metadata)


# ------------------------------------------------------------------------------
# 7. Run the App
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # To run this file:
    # 1. Install the necessary packages: pip install "fastapi[all]"
    # 2. Run the server: uvicorn main:app --reload
    # 3. Open your browser to http://127.0.0.1:8000/docs to see the interactive API documentation.
    uvicorn.run(app, host="0.0.0.0", port=8001)

