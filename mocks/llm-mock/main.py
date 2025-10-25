#!/usr/bin/env python3
"""
Mock LLM Server - Testing Support

This provides a mock LLM service for testing purposes, similar to mcp-http-mock.
It simulates OpenAI-compatible API responses for testing chat functionality.
"""

import json
import time
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Mock LLM Server", description="Mock LLM service for testing")

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

def generate_mock_response(messages: List[ChatMessage]) -> str:
    """Generate appropriate mock response based on the input."""
    if not messages:
        return MOCK_RESPONSES["default"]
    
    last_message = messages[-1].content.lower()
    
    # Simple keyword matching for different responses
    if any(word in last_message for word in ["hello", "hi", "greetings"]):
        return MOCK_RESPONSES["greeting"]
    elif "test" in last_message:
        return MOCK_RESPONSES["test"]
    elif "error" in last_message:
        return MOCK_RESPONSES["error"]
    elif "long" in last_message:
        return MOCK_RESPONSES["long"]
    elif "json" in last_message:
        return MOCK_RESPONSES["json"]
    elif "code" in last_message:
        return MOCK_RESPONSES["code"]
    else:
        return MOCK_RESPONSES["default"]

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """Mock OpenAI chat completions endpoint."""
    
    # Simulate processing time
    time.sleep(0.1)
    
    # Generate mock response
    response_content = generate_mock_response(request.messages)
    
    # Create mock usage statistics
    prompt_tokens = sum(len(msg.content.split()) for msg in request.messages)
    completion_tokens = len(response_content.split())
    
    response = ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:29]}",
        object="chat.completion",
        created=int(time.time()),
        model=request.model,
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
        "version": "1.0.0",
        "description": "Mock LLM service for testing chat applications",
        "endpoints": {
            "/v1/chat/completions": "POST - Chat completions",
            "/v1/models": "GET - List available models",
            "/health": "GET - Health check",
            "/test/scenario/{scenario}": "POST - Set test scenarios"
        }
    }

if __name__ == "__main__":
    print("Starting Mock LLM Server...")
    print("Available endpoints:")
    print("  - POST /v1/chat/completions - Mock chat completions")
    print("  - GET /v1/models - List mock models")
    print("  - GET /health - Health check")
    print("  - POST /test/scenario/{scenario} - Test scenarios")
    
    uvicorn.run(app, host="127.0.0.1", port=8001)