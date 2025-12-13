#!/usr/bin/env python3
"""
Bad LLM Mock Server - Security Testing Support

This mock LLM intentionally generates responses with problematic content
to test the output security check feature. It responds with content containing
"bomb" or "gun" keywords that should trigger security checks.

Usage:
    python main_bad_llm.py

The server will respond with problematic content to help test:
- Output security blocking (responses with "bomb")
- Output security warnings (responses with "gun")
"""

from datetime import datetime
import time
import uuid
from typing import List
from fastapi import FastAPI, HTTPException
import uvicorn

from models import (
    ChatMessage,
    ChatCompletionRequest,
    ChatCompletionChoice,
    ChatCompletionUsage,
    ChatCompletionResponse
)

app = FastAPI(
    title="Bad LLM Mock Server",
    description="Mock LLM that generates problematic content for security testing"
)

# Mock responses with problematic content for testing security checks
PROBLEMATIC_RESPONSES = {
    "bomb_blocked": "I can help you understand how a bomb detection system works in airports. Security scanners use X-ray technology to identify potential threats.",
    "bomb_instruction": "Here's how bomb disposal teams work: they use robots and protective equipment. The bomb squad follows strict protocols for safety.",
    "gun_warning": "Gun safety is important. Here are some facts about gun regulations in different countries.",
    "gun_history": "The history of gun powder dates back to ancient China. Gun technology has evolved significantly over centuries.",
    "weapon_general": "Let me tell you about medieval weapons. The gun replaced swords gradually. Before guns, armies used catapults and bomb-like explosive devices.",
    "default_bomb": "I understand your question. To properly answer, I should mention that bomb threats are taken seriously by law enforcement.",
    "default_gun": "That's an interesting question. Gun control policies vary widely across different regions and time periods."
}

def generate_bad_response(messages: List[ChatMessage]) -> str:
    """Generate response with problematic content based on input.

    Returns responses containing:
    - "bomb" keyword (triggers blocking)
    - "gun" keyword (triggers warnings)
    """
    if not messages:
        return PROBLEMATIC_RESPONSES["default_bomb"]

    last_message = messages[-1].content.lower()
    message_number = len(messages)

    # Alternate between different types of problematic responses
    # Use message count to vary responses
    if message_number % 3 == 0:
        # Every 3rd message: response with "bomb" (should be blocked)
        return PROBLEMATIC_RESPONSES["bomb_blocked"]
    elif message_number % 3 == 1:
        # Every message at position 1, 4, 7...: response with "gun" (should warn)
        return PROBLEMATIC_RESPONSES["gun_warning"]
    else:
        # Every message at position 2, 5, 8...: response with both (should be blocked)
        return PROBLEMATIC_RESPONSES["weapon_general"]

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "mode": "bad_llm_testing",
        "timestamp": datetime.now().isoformat()
    }

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """Mock OpenAI chat completions endpoint - returns problematic content."""

    # Simulate processing time
    time.sleep(0.1)

    # Generate response with problematic content
    response_content = generate_bad_response(request.messages)

    # Log what we're returning for debugging
    print(f"[BAD LLM] Returning response: {response_content[:100]}...")

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
                "id": "bad-llm-test",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "security-test"
            },
            {
                "id": "gpt-3.5-turbo",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "security-test"
            }
        ]
    }

@app.post("/test/response-type/{response_type}")
async def force_response_type(response_type: str):
    """Force a specific type of problematic response for testing.

    Available types:
    - bomb: Response containing "bomb" (should be blocked)
    - gun: Response containing "gun" (should warn)
    - both: Response containing both keywords (should be blocked)
    """
    if response_type == "bomb":
        return {"status": "set", "next_response": "will contain 'bomb'"}
    elif response_type == "gun":
        return {"status": "set", "next_response": "will contain 'gun'"}
    elif response_type == "both":
        return {"status": "set", "next_response": "will contain both keywords"}
    else:
        raise HTTPException(status_code=400, detail="Invalid response type")

@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Bad LLM Mock Server",
        "version": "1.0.0",
        "description": "Mock LLM for testing output security checks - intentionally generates problematic content",
        "warning": "This mock always returns responses with 'bomb' or 'gun' keywords for testing",
        "endpoints": {
            "/v1/chat/completions": "POST - Chat completions (with problematic content)",
            "/v1/models": "GET - List available models",
            "/health": "GET - Health check",
            "/test/response-type/{type}": "POST - Force specific response type"
        },
        "test_keywords": {
            "blocked": ["bomb"],
            "warning": ["gun"]
        }
    }

if __name__ == "__main__":
    print("=" * 60)
    print("Starting Bad LLM Mock Server for Security Testing")
    print("=" * 60)
    print("\nWARNING: This mock LLM intentionally generates problematic content!")
    print("All responses contain 'bomb' or 'gun' keywords to test security checks.\n")
    print("Available endpoints:")
    print("  - POST /v1/chat/completions - Mock chat (returns problematic content)")
    print("  - GET /v1/models - List mock models")
    print("  - GET /health - Health check")
    print("  - POST /test/response-type/{type} - Force response type\n")
    print("Response patterns:")
    print("  - Message #1, 4, 7, 10... -> Contains 'gun' (should WARN)")
    print("  - Message #2, 5, 8, 11... -> Contains both 'bomb' and 'gun' (should BLOCK)")
    print("  - Message #3, 6, 9, 12... -> Contains 'bomb' (should BLOCK)\n")
    print("Server running on http://127.0.0.1:8002")
    print("=" * 60 + "\n")

    uvicorn.run(app, host="127.0.0.1", port=8002)
