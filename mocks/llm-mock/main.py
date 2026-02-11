#!/usr/bin/env python3
"""
Mock LLM Server - Testing Support

This provides a mock LLM service for testing purposes, similar to mcp-http-mock.
It simulates OpenAI-compatible API responses for testing chat functionality.

Per-user API key support:
    Set MOCK_LLM_REQUIRE_AUTH=true to enable Bearer token validation.
    Any non-empty Bearer token is accepted. The token value and a timestamp
    are printed to stdout on every authenticated request so you can verify
    that per-user keys flow end-to-end through the system.
"""

import os
from datetime import datetime
import json
import time
import uuid
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
import uvicorn

REQUIRE_AUTH = os.environ.get("MOCK_LLM_REQUIRE_AUTH", "false").lower() in ("true", "1", "yes")
MOCK_LLM_PORT = int(os.environ.get("MOCK_LLM_PORT", "8002"))

# Known API keys mapped to user names for testing.
# Paste one of these into the UI when prompted for a per-user key.
API_KEY_TO_USER: Dict[str, str] = {
    "sk-alice-test-key-001": "alice@example.com",
    "sk-bob-test-key-002": "bob@example.com",
    "sk-charlie-test-key-003": "charlie@example.com",
}

app = FastAPI(title="Mock LLM Server", description="Mock LLM service for testing")


def _extract_bearer_token(request: Request) -> Optional[str]:
    """Extract Bearer token from Authorization header, or None."""
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _validate_auth(request: Request) -> Optional[str]:
    """Validate auth when MOCK_LLM_REQUIRE_AUTH is enabled.

    Returns the token value on success.
    Raises HTTPException(401) if auth is missing/invalid.
    Returns None when auth is disabled.
    """
    if not REQUIRE_AUTH:
        return None
    token = _extract_bearer_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    return token


class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    max_tokens: Optional[int] = 1000
    temperature: Optional[float] = 0.7
    stream: Optional[bool] = False

class ChatCompletionChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str

class ChatCompletionUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class ChatCompletionResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage

# Mock responses for different scenarios
MOCK_RESPONSES = {
    "greeting": "Hello! I'm a mock LLM assistant. How can I help you today?",
    "test": "This is a test response from the mock LLM service.",
    "error": "I'm sorry, I encountered an error processing your request.",
    "long": "This is a longer response to test how the system handles more verbose outputs. " * 10,
    "json": '{"message": "This is a JSON response", "status": "success", "data": {"key": "value"}}',
    "code": "```python\nprint('Hello, world!')\n```",
    "default": "I understand your message. This is a mock response for testing purposes."
}

def generate_mock_response(messages: List[ChatMessage], api_key: Optional[str] = None) -> str:
    """Generate appropriate mock response based on the input."""
    if not messages:
        return MOCK_RESPONSES["default"]

    last_message = messages[-1].content.lower()

    # Simple keyword matching for different responses
    if any(word in last_message for word in ["hello", "hi", "greetings"]):
        base = MOCK_RESPONSES["greeting"]
    elif "test" in last_message:
        base = MOCK_RESPONSES["test"]
    elif "error" in last_message:
        base = MOCK_RESPONSES["error"]
    elif "long" in last_message:
        base = MOCK_RESPONSES["long"]
    elif "json" in last_message:
        base = MOCK_RESPONSES["json"]
    elif "code" in last_message:
        base = MOCK_RESPONSES["code"]
    else:
        base = MOCK_RESPONSES["default"]

    # When auth is enabled, include a note about the key in the response
    if api_key:
        user = API_KEY_TO_USER.get(api_key, "unknown-user")
        base += f" [Authenticated as {user}]"

    return base

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "auth_required": REQUIRE_AUTH,
        "timestamp": datetime.now().isoformat(),
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Mock OpenAI chat completions endpoint."""

    # Validate auth if enabled
    api_key = _validate_auth(request)
    if api_key:
        user = API_KEY_TO_USER.get(api_key, "unknown-user")
        masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "****"
        print(f"[AUTH] {datetime.now().isoformat()} | user={user} | key={masked}")

    # Parse body
    body = await request.json()
    completion_request = ChatCompletionRequest(**body)

    # Simulate processing time
    time.sleep(0.1)

    # Generate mock response
    response_content = generate_mock_response(completion_request.messages, api_key=api_key)

    # Create mock usage statistics
    prompt_tokens = sum(len(msg.content.split()) for msg in completion_request.messages)
    completion_tokens = len(response_content.split())

    response = ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:29]}",
        object="chat.completion",
        created=int(time.time()),
        model=completion_request.model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ChatMessage(role="assistant", content=response_content),
                finish_reason="stop"
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens
        )
    )

    return response

@app.get("/v1/models")
async def list_models():
    """Mock models endpoint."""
    return {
        "object": "list",
        "data": [
            {
                "id": "gpt-3.5-turbo",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mock-llm"
            },
            {
                "id": "gpt-4",
                "object": "model", 
                "created": int(time.time()),
                "owned_by": "mock-llm"
            },
            {
                "id": "mock-model",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mock-llm"
            }
        ]
    }

@app.post("/test/scenario/{scenario}")
async def set_test_scenario(scenario: str, response_data: Dict[str, Any] = None):
    """Set specific test scenario for controlled testing."""
    if scenario == "error":
        raise HTTPException(status_code=500, detail="Mock error for testing")
    elif scenario == "timeout":
        time.sleep(10)  # Simulate timeout
        return {"status": "timeout"}
    elif scenario == "custom" and response_data:
        return response_data
    else:
        return {"scenario": scenario, "status": "set"}

@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Mock LLM Server",
        "version": "1.1.0",
        "description": "Mock LLM service for testing chat applications",
        "auth_required": REQUIRE_AUTH,
        "endpoints": {
            "/v1/chat/completions": "POST - Chat completions",
            "/v1/models": "GET - List available models",
            "/health": "GET - Health check",
            "/test/scenario/{scenario}": "POST - Set test scenarios"
        }
    }

if __name__ == "__main__":
    print(f"Starting Mock LLM Server on port {MOCK_LLM_PORT}...")
    print(f"  Auth required: {REQUIRE_AUTH}")
    if REQUIRE_AUTH:
        print("  Valid test keys:")
        for key, user in API_KEY_TO_USER.items():
            print(f"    {key}  ->  {user}")
    print("Available endpoints:")
    print("  - POST /v1/chat/completions - Mock chat completions")
    print("  - GET /v1/models - List mock models")
    print("  - GET /health - Health check")
    print("  - POST /test/scenario/{scenario} - Test scenarios")

    uvicorn.run(app, host="127.0.0.1", port=MOCK_LLM_PORT)
