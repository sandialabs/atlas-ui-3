#!/usr/bin/env python3
"""
Mock LLM Server - Testing Support (Rate Limit / Error Simulation Variant)

This provides a mock LLM service for testing purposes with rate limiting and random errors.
It simulates OpenAI-compatible API responses for testing reliability and error handling.
"""

import json
import time
import uuid
import random
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Mock LLM Server (Rate Limit & Error Simulation)", description="Mock LLM service with reliability testing features")

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

# Rate limiting
class RateLimiter:
    def __init__(self, requests_per_minute: int = 10):
        self.requests_per_minute = requests_per_minute
        self.requests = []
        self.lockout_until = None

    def is_allowed(self) -> bool:
        now = datetime.now()

        # Check if we're currently in a lockout period
        if self.lockout_until and now < self.lockout_until:
            return False

        # Clean old requests (older than 1 minute)
        one_minute_ago = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
        self.requests = [req for req in self.requests if req > one_minute_ago]

        # Check if we're under the limit
        if len(self.requests) < self.requests_per_minute:
            self.requests.append(now)
            return True

        # Rate limit exceeded - lockout for 30 seconds
        self.lockout_until = now.replace(second=0, microsecond=0) + timedelta(seconds=30)
        logger.warning("Rate limit exceeded, locking out for 30 seconds")
        return False

from datetime import timedelta
rate_limiter = RateLimiter(requests_per_minute=5)  # More restrictive for testing

# Mock responses for different scenarios
MOCK_RESPONSES = {
    "greeting": "Hello! I'm a mock LLM assistant with rate limiting enabled. How can I help you today?",
    "test": "This is a test response from the rate-limited mock LLM service.",
    "error": "I'm sorry, I encountered an error processing your request.",
    "long": "This is a longer response to test how the system handles more verbose outputs under rate limiting. " * 10,
    "json": '{"message": "This is a JSON response from rate-limited service", "status": "success", "data": {"key": "value"}}',
    "code": "```python\nprint('Hello from rate-limited mock!')\n```",
    "rate_limited": "You've hit the rate limit! Please wait before making another request.",
    "server_error": "Internal Server Error - simulated failure for testing",
    "network_error": "Network timeout - simulated network issue",
    "default": "I understand your message. This is a mock response with reliability features."
}

def should_simulate_error() -> Optional[str]:
    """Randomly decide whether to simulate an error (10% chance)."""
    error_types = ["server_error", "network_error", None, None, None, None]  #  error rate
    error_type = random.choice(error_types)

    if error_type:
        logger.warning(f"Simulating {error_type} for testing")
        return error_type
    return None

def add_random_delay():
    """Add random delays to simulate network latency."""
    # 30% chance of delay between 0.1-2 seconds
    if random.random() < 0.3:
        delay = random.uniform(0.1, 2.0)
        logger.info(f"Adding artificial delay of {delay:.2f} seconds")
        time.sleep(delay)

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
    """Health check endpoint with rate limiting simulation."""
    logger.info("Health check requested")

    # Simulate occasional health check failures
    if random.random() < 0.05:  # 5% chance of health check failure
        logger.error("Simulated health check failure")
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    add_random_delay()
    return {"status": "healthy", "timestamp": datetime.now().isoformat(), "rate_limiter": "active"}

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    """Mock OpenAI chat completions endpoint with rate limiting and errors."""

    # Check rate limit first
    if not rate_limiter.is_allowed():
        logger.warning("Rate limit exceeded for chat completion")
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Please try again later."
        )

    logger.info(f"Chat completion requested for model: {request.model}")

    # Simulate random errors
    error_type = should_simulate_error()
    if error_type:
        if error_type == "server_error":
            raise HTTPException(status_code=500, detail="Internal server error")
        elif error_type == "network_error":
            # Simulate network timeout by sleeping
            time.sleep(5)
            raise HTTPException(status_code=504, detail="Gateway timeout")

    # Add artificial delay
    add_random_delay()

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
    """Mock models endpoint with occasional errors."""
    logger.info("Models list requested")

    # Check rate limit
    if not rate_limiter.is_allowed():
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Simulate occasional model listing failures
    if random.random() < 0.1:  # 10% chance
        logger.error("Simulated model listing failure")
        raise HTTPException(status_code=503, detail="Model service temporarily unavailable")

    add_random_delay()

    return {
        "object": "list",
        "data": [
            {
                "id": "gpt-3.5-turbo",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mock-llm-rate-limited"
            },
            {
                "id": "gpt-4",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mock-llm-rate-limited"
            },
            {
                "id": "mock-model",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mock-llm-rate-limited"
            }
        ]
    }

@app.post("/test/scenario/{scenario}")
async def set_test_scenario(scenario: str, response_data: Dict[str, Any] = None):
    """Set specific test scenario for controlled testing."""
    logger.info(f"Test scenario set: {scenario}")

    # Check rate limit
    if not rate_limiter.is_allowed():
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    if scenario == "error":
        raise HTTPException(status_code=500, detail="Mock error for testing")
    elif scenario == "timeout":
        time.sleep(10)  # Simulate timeout
        return {"status": "timeout"}
    elif scenario == "rate_limit":
        # Force rate limit exceeded for testing
        rate_limiter.lockout_until = datetime.now() + timedelta(seconds=30)
        raise HTTPException(status_code=429, detail="Forced rate limit for testing")
    elif scenario == "custom" and response_data:
        return response_data
    else:
        return {"scenario": scenario, "status": "set", "rate_limiting": "active"}

@app.get("/status")
async def get_status():
    """Get current server status including rate limiter state."""
    remaining_requests = max(0, rate_limiter.requests_per_minute - len(rate_limiter.requests))

    return {
        "status": "operational",
        "rate_limiter": {
            "requests_per_minute": rate_limiter.requests_per_minute,
            "current_requests": len(rate_limiter.requests),
            "remaining": remaining_requests,
            "lockout_active": rate_limiter.lockout_until is not None and datetime.now() < rate_limiter.lockout_until
        },
        "features": ["rate_limiting", "error_simulation", "random_delays"]
    }

@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Mock LLM Server (Rate Limit & Error Simulation)",
        "version": "1.1.0",
        "description": "Mock LLM service with rate limiting and reliability testing features",
        "endpoints": {
            "/v1/chat/completions": "POST - Chat completions (rate limited)",
            "/v1/models": "GET - List available models",
            "/health": "GET - Health check",
            "/status": "GET - Server status and rate limiter info",
            "/test/scenario/{scenario}": "POST - Set test scenarios"
        },
        "features": {
            "rate_limiting": "5 requests per minute",
            "error_simulation": "10% random error rate",
            "delays": "Random network delays",
            "logging": "Comprehensive request logging"
        }
    }

if __name__ == "__main__":
    print("Starting Mock LLM Server with Rate Limiting & Error Simulation...")
    print("Available endpoints:")
    print("  - POST /v1/chat/completions - Mock chat completions (rate limited)")
    print("  - GET /v1/models - List mock models")
    print("  - GET /health - Health check")
    print("  - GET /status - Server status")
    print("  - POST /test/scenario/{scenario} - Test scenarios")
    print()
    print("Features:")
    print("  - Rate limiting: 5 requests per minute")
    print("  - Random errors: ~10% of requests")
    print("  - Network delays: Occasional artificial delays")
    print("  - Comprehensive logging")

    uvicorn.run(app, host="127.0.0.1", port=8002)
