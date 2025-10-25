"""CLI interface for LLM operations.

This CLI allows you to:
- Call LLMs with plain text
- Test LLM calls with tools
- Test RAG-integrated LLM calls
- Validate LLM configurations
"""

import argparse
import json
import logging
import sys
from typing import List, Dict

from .litellm_caller import LiteLLMCaller
from .models import LLMResponse

# Set up logging for CLI
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


async def call_plain(args) -> None:
    """Make a plain LLM call."""
    if not args.model:
        print("❌ Model name is required")
        return
    
    if not args.message:
        print("❌ Message is required")
        return
    
    print(f"🤖 Calling {args.model} with plain message...")
    
    try:
        llm_caller = LiteLLMCaller()
        
        # Prepare messages
        messages = [{"role": "user", "content": args.message}]
        if args.system:
            messages.insert(0, {"role": "system", "content": args.system})
        
        # Make the call
        response = await llm_caller.call_plain(args.model, messages)
        
        print(f"\n✅ Response from {args.model}:\n")
        print(response)
        
    except Exception as e:
        print(f"❌ LLM call failed: {e}")
        logger.error(f"LLM call error: {e}")


async def call_with_tools(args) -> None:
    """Make an LLM call with tools."""
    if not args.model:
        print("❌ Model name is required")
        return
    
    if not args.message:
        print("❌ Message is required")
        return
    
    # Parse tools if provided
    tools_schema = []
    if args.tools:
        for tool_name in args.tools:
            # Create simple test tool schemas
            if tool_name == "calculator":
                tools_schema.append({
                    "type": "function",
                    "function": {
                        "name": "calculator_add",
                        "description": "Add two numbers",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "a": {"type": "number", "description": "First number"},
                                "b": {"type": "number", "description": "Second number"}
                            },
                            "required": ["a", "b"]
                        }
                    }
                })
            elif tool_name == "weather":
                tools_schema.append({
                    "type": "function",
                    "function": {
                        "name": "weather_get",
                        "description": "Get weather information",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string", "description": "City name"}
                            },
                            "required": ["location"]
                        }
                    }
                })
    
    print(f"🔧 Calling {args.model} with {len(tools_schema)} tools...")
    
    try:
        llm_caller = LiteLLMCaller()
        
        # Prepare messages
        messages = [{"role": "user", "content": args.message}]
        if args.system:
            messages.insert(0, {"role": "system", "content": args.system})
        
        # Make the call
        response = await llm_caller.call_with_tools(
            args.model, 
            messages, 
            tools_schema, 
            args.tool_choice
        )
        
        print(f"\n✅ Response from {args.model}:\n")
        
        if response.content:
            print("💬 Content:")
            print(response.content)
        
        if response.has_tool_calls():
            print(f"\n🔧 Tool Calls ({len(response.tool_calls)}):")
            for i, tool_call in enumerate(response.tool_calls, 1):
                print(f"   {i}. {tool_call['function']['name']}")
                print(f"      Arguments: {tool_call['function']['arguments']}")
        
    except Exception as e:
        print(f"❌ LLM call failed: {e}")
        logger.error(f"LLM call error: {e}")


async def test_rag_call(args) -> None:
    """Test LLM call with RAG integration."""
    if not args.model:
        print("❌ Model name is required")
        return
    
    if not args.message:
        print("❌ Message is required")
        return
    
    if not args.user_email:
        print("❌ User email is required for RAG calls")
        return
    
    data_sources = args.data_sources or ["test-datasource"]
    
    print(f"📚 Calling {args.model} with RAG from sources: {', '.join(data_sources)}...")
    
    try:
        llm_caller = LiteLLMCaller()
        
        # Prepare messages
        messages = [{"role": "user", "content": args.message}]
        if args.system:
            messages.insert(0, {"role": "system", "content": args.system})
        
        # Make the call
        response = await llm_caller.call_with_rag(
            args.model, 
            messages, 
            data_sources,
            args.user_email
        )
        
        print(f"\n✅ RAG-enhanced response from {args.model}:\n")
        print(response)
        
    except Exception as e:
        print(f"❌ RAG call failed: {e}")
        logger.error(f"RAG call error: {e}")


def list_models(args) -> None:
    """List available LLM models."""
    try:
        llm_caller = LiteLLMCaller()
        models = llm_caller.llm_config.models
        
        if not models:
            print("❌ No models configured")
            return
        
        print(f"🤖 Available LLM Models ({len(models)}):\n")
        
        for name, model in models.items():
            print(f"📋 {name}")
            print(f"   Model ID: {model.model_name}")
            print(f"   API URL: {model.model_url}")
            print(f"   Max Tokens: {model.max_tokens}")
            print(f"   Temperature: {model.temperature}")
            if model.description:
                print(f"   Description: {model.description}")
            print()
        
    except Exception as e:
        print(f"❌ Failed to list models: {e}")
        logger.error(f"List models error: {e}")


def validate_model(args) -> None:
    """Validate a specific model configuration."""
    if not args.model:
        print("❌ Model name is required")
        return
    
    try:
        llm_caller = LiteLLMCaller()
        
        if args.model not in llm_caller.llm_config.models:
            print(f"❌ Model '{args.model}' not found in configuration")
            return
        
        model_config = llm_caller.llm_config.models[args.model]
        
        print(f"🔍 Validating model configuration for: {args.model}\n")
        
        # Check basic configuration
        print("📋 Configuration:")
        print(f"   ✅ Model ID: {model_config.model_name}")
        print(f"   ✅ API URL: {model_config.model_url}")
        print(f"   ✅ Max Tokens: {model_config.max_tokens}")
        print(f"   ✅ Temperature: {model_config.temperature}")
        
        # Check API key
        import os
        api_key = os.path.expandvars(model_config.api_key)
        if api_key and not api_key.startswith("${"):
            print(f"   ✅ API Key: Configured")
        else:
            print(f"   ❌ API Key: Missing or not resolved")
        
        # Check extra headers
        if model_config.extra_headers:
            print(f"   ✅ Extra Headers: {list(model_config.extra_headers.keys())}")
        else:
            print(f"   ℹ️  Extra Headers: None")
        
        print(f"\n🎉 Model '{args.model}' configuration looks valid!")
        
    except Exception as e:
        print(f"❌ Validation failed: {e}")
        logger.error(f"Validation error: {e}")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="LLM operations CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m backend.modules.llm.cli call gpt-4.1 "Hello, how are you?"
  python -m backend.modules.llm.cli call-with-tools gpt-4.1 "Calculate 5+3" --tools calculator
  python -m backend.modules.llm.cli test-rag gpt-4.1 "What is machine learning?" --user-email test@example.com
  python -m backend.modules.llm.cli list-models
  python -m backend.modules.llm.cli validate gpt-4.1
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Call command
    call_parser = subparsers.add_parser('call', help='Make a plain LLM call')
    call_parser.add_argument('model', help='Model name to use')
    call_parser.add_argument('message', help='Message to send')
    call_parser.add_argument('--system', help='System prompt')
    call_parser.set_defaults(func=call_plain)
    
    # Call with tools command
    tools_parser = subparsers.add_parser('call-with-tools', help='Make LLM call with tools')
    tools_parser.add_argument('model', help='Model name to use')
    tools_parser.add_argument('message', help='Message to send')
    tools_parser.add_argument('--system', help='System prompt')
    tools_parser.add_argument('--tools', nargs='+', choices=['calculator', 'weather'], help='Tools to include')
    tools_parser.add_argument('--tool-choice', default='auto', help='Tool choice mode')
    tools_parser.set_defaults(func=call_with_tools)
    
    # RAG test command
    rag_parser = subparsers.add_parser('test-rag', help='Test LLM call with RAG integration')
    rag_parser.add_argument('model', help='Model name to use')
    rag_parser.add_argument('message', help='Message to send')
    rag_parser.add_argument('--user-email', required=True, help='User email for RAG context')
    rag_parser.add_argument('--system', help='System prompt')
    rag_parser.add_argument('--data-sources', nargs='+', help='Data sources to query')
    rag_parser.set_defaults(func=test_rag_call)
    
    # List models command
    list_parser = subparsers.add_parser('list-models', help='List all available models')
    list_parser.set_defaults(func=list_models)
    
    # Validate model command
    validate_parser = subparsers.add_parser('validate', help='Validate a model configuration')
    validate_parser.add_argument('model', help='Model name to validate')
    validate_parser.set_defaults(func=validate_model)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        if hasattr(args, 'func'):
            if args.command in ['call', 'call-with-tools', 'test-rag']:
                # Async commands
                import asyncio
                asyncio.run(args.func(args))
            else:
                # Sync commands
                args.func(args)
    except KeyboardInterrupt:
        print("\n⚠️  Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()