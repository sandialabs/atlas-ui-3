#!/usr/bin/env python3
"""
Quick test script to verify sampling functionality works end-to-end.
This tests the sampling_demo server directly to ensure it can be initialized
and sampling works correctly.
"""

import asyncio
import sys
import logging
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_sampling_demo():
    """Test the sampling_demo server."""
    try:
        from fastmcp import Client
        from modules.llm.litellm_caller import LiteLLMCaller
        from modules.config import config_manager
        
        logger.info("Starting sampling demo test...")
        
        # Create sampling handler that uses LiteLLM
        async def sampling_handler(messages, params, context):
            from mcp.types import CreateMessageResult, TextContent
            
            logger.info(f"Sampling handler called with {len(messages)} messages")
            
            # For testing purposes, we'll return a mock response
            # In production, this would call the actual LLM
            logger.info("Returning mock LLM response for testing")
            
            # Return proper CreateMessageResult
            return CreateMessageResult(
                role="assistant",
                content=TextContent(type="text", text="This is a mock LLM response for testing purposes."),
                model="mock-model"
            )
        
        # Create client to sampling_demo server
        server_path = "backend/mcp/sampling_demo/main.py"
        logger.info(f"Connecting to server: {server_path}")
        
        client = Client(server_path, sampling_handler=sampling_handler)
        
        async with client:
            # List tools
            tools = await client.list_tools()
            logger.info(f"Available tools: {[t.name for t in tools]}")
            
            # Test summarize_text tool
            logger.info("\n=== Testing summarize_text ===")
            result = await client.call_tool(
                "summarize_text",
                {
                    "text": "The quick brown fox jumps over the lazy dog. "
                           "This sentence contains every letter of the alphabet. "
                           "It is commonly used for testing fonts and keyboards."
                }
            )
            logger.info(f"Result: {result}")
            
            # Test analyze_sentiment tool
            logger.info("\n=== Testing analyze_sentiment ===")
            result = await client.call_tool(
                "analyze_sentiment",
                {
                    "text": "I absolutely love this product! It's amazing and works perfectly!"
                }
            )
            logger.info(f"Result: {result}")
            
        logger.info("\n✅ All tests passed!")
        return True
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        return False


if __name__ == "__main__":
    success = asyncio.run(test_sampling_demo())
    sys.exit(0 if success else 1)
