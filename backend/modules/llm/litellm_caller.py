"""
LiteLLM-based LLM calling interface that handles all modes of LLM interaction.

This module provides a clean interface for calling LLMs using LiteLLM in different modes:
- Plain LLM calls (no tools)
- LLM calls with RAG integration
- LLM calls with tool support
- LLM calls with both RAG and tools

LiteLLM provides unified access to multiple LLM providers with automatic
fallbacks, cost tracking, and provider-specific optimizations.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import litellm
from litellm import completion, acompletion
from .models import LLMResponse

logger = logging.getLogger(__name__)

# Configure LiteLLM settings
litellm.drop_params = True  # Drop unsupported params instead of erroring


class LiteLLMCaller:
    """Clean interface for all LLM calling patterns using LiteLLM."""
    
    def __init__(self, llm_config=None, debug_mode: bool = False):
        """Initialize with optional config dependency injection."""
        if llm_config is None:
            from modules.config import config_manager
            self.llm_config = config_manager.llm_config
        else:
            self.llm_config = llm_config
        
        # Set litellm verbosity based on debug mode
        litellm.set_verbose = debug_mode
    
    def _get_litellm_model_name(self, model_name: str) -> str:
        """Convert internal model name to LiteLLM compatible format."""
        if model_name not in self.llm_config.models:
            raise ValueError(f"Model {model_name} not found in configuration")
        
        model_config = self.llm_config.models[model_name]
        model_id = model_config.model_name
        
        # Map common providers to LiteLLM format
        if "openrouter" in model_config.model_url:
            return f"openrouter/{model_id}"
        elif "openai" in model_config.model_url:
            return f"openai/{model_id}"
        elif "anthropic" in model_config.model_url:
            return f"anthropic/{model_id}"
        elif "google" in model_config.model_url:
            return f"google/{model_id}"
        else:
            # For custom endpoints, use the model_id directly
            return model_id
    
    def _get_model_kwargs(self, model_name: str, temperature: Optional[float] = None) -> Dict[str, Any]:
        """Get LiteLLM kwargs for a specific model."""
        if model_name not in self.llm_config.models:
            raise ValueError(f"Model {model_name} not found in configuration")
        
        model_config = self.llm_config.models[model_name]
        kwargs = {
            "max_tokens": model_config.max_tokens or 1000,
        }
        
        # Use provided temperature or fall back to config temperature
        if temperature is not None:
            kwargs["temperature"] = temperature
        else:
            kwargs["temperature"] = model_config.temperature or 0.7
        
        # Set API key
        api_key = os.path.expandvars(model_config.api_key)
        if api_key and not api_key.startswith("${"):
            if "openrouter" in model_config.model_url:
                kwargs["api_key"] = api_key
                # LiteLLM will automatically set the correct env var
                os.environ["OPENROUTER_API_KEY"] = api_key
            elif "openai" in model_config.model_url:
                os.environ["OPENAI_API_KEY"] = api_key
            elif "anthropic" in model_config.model_url:
                os.environ["ANTHROPIC_API_KEY"] = api_key
            elif "google" in model_config.model_url:
                os.environ["GOOGLE_API_KEY"] = api_key
        
        # Set custom API base for non-standard endpoints
        if hasattr(model_config, 'model_url') and model_config.model_url:
            if not any(provider in model_config.model_url for provider in ["openrouter", "api.openai.com", "api.anthropic.com"]):
                kwargs["api_base"] = model_config.model_url
        
        return kwargs

    async def call_plain(self, model_name: str, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        """Plain LLM call - no tools, no RAG."""
        litellm_model = self._get_litellm_model_name(model_name)
        model_kwargs = self._get_model_kwargs(model_name, temperature)
        
        try:
            total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            logger.info(f"Plain LLM call: {len(messages)} messages, {total_chars} chars")
            
            response = await acompletion(
                model=litellm_model,
                messages=messages,
                **model_kwargs
            )
            
            content = response.choices[0].message.content or ""
            logger.info(f"LLM response preview: '{content[:200]}{'...' if len(content) > 200 else ''}'")
            return content
            
        except Exception as exc:
            logger.error("Error calling LLM: %s", exc, exc_info=True)
            raise Exception(f"Failed to call LLM: {exc}")
    
    async def call_with_rag(
        self, 
        model_name: str, 
        messages: List[Dict[str, str]], 
        data_sources: List[str],
        user_email: str,
        rag_client=None,
        temperature: float = 0.7,
    ) -> str:
        """LLM call with RAG integration."""
        if not data_sources:
            return await self.call_plain(model_name, messages, temperature=temperature)
        
        # Import RAG client if not provided
        if rag_client is None:
            from modules.rag import rag_client as default_rag_client
            rag_client = default_rag_client
        
        # Use the first selected data source
        data_source = data_sources[0]
        
        try:
            # Query RAG for context
            rag_response = await rag_client.query_rag(
                user_email,
                data_source,
                messages
            )
            
            # Integrate RAG context into messages
            messages_with_rag = messages.copy()
            rag_context_message = {
                "role": "system", 
                "content": f"Retrieved context from {data_source}:\n\n{rag_response.content}\n\nUse this context to inform your response."
            }
            messages_with_rag.insert(-1, rag_context_message)
            
            # Call LLM with enriched context
            llm_response = await self.call_plain(model_name, messages_with_rag, temperature=temperature)
            
            # Append metadata if available
            if rag_response.metadata:
                metadata_summary = self._format_rag_metadata(rag_response.metadata)
                llm_response += f"\n\n---\n**RAG Sources & Processing Info:**\n{metadata_summary}"
            
            return llm_response
            
        except Exception as exc:
            logger.error(f"Error in RAG-integrated query: {exc}")
            # Fallback to plain LLM call
            return await self.call_plain(model_name, messages, temperature=temperature)
    
    async def call_with_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        tools_schema: List[Dict],
        tool_choice: str = "auto",
        temperature: float = 0.7,
    ) -> LLMResponse:
        """LLM call with tool support using LiteLLM."""
        if not tools_schema:
            content = await self.call_plain(model_name, messages, temperature=temperature)
            return LLMResponse(content=content, model_used=model_name)

        litellm_model = self._get_litellm_model_name(model_name)
        model_kwargs = self._get_model_kwargs(model_name, temperature)
        
        # Handle tool_choice parameter - some providers don't support "required"
        final_tool_choice = tool_choice
        if tool_choice == "required":
            # Try with "required" first, fallback to "auto" if unsupported
            final_tool_choice = "auto"
            logger.info(f"Using tool_choice='auto' instead of 'required' for better compatibility")

        try:
            total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            logger.info(f"LLM call with tools: {len(messages)} messages, {total_chars} chars, {len(tools_schema)} tools")
            
            response = await acompletion(
                model=litellm_model,
                messages=messages,
                tools=tools_schema,
                tool_choice=final_tool_choice,
                **model_kwargs
            )
            
            message = response.choices[0].message
            return LLMResponse(
                content=getattr(message, 'content', None) or "",
                tool_calls=getattr(message, 'tool_calls', None),
                model_used=model_name
            )
            
        except Exception as exc:
            # If we used "required" and it failed, try again with "auto"
            if tool_choice == "required" and final_tool_choice == "required":
                logger.warning(f"Tool choice 'required' failed, retrying with 'auto': {exc}")
                try:
                    response = await acompletion(
                        model=litellm_model,
                        messages=messages,
                        tools=tools_schema,
                        tool_choice="auto",
                        **model_kwargs
                    )
                    
                    message = response.choices[0].message
                    return LLMResponse(
                        content=getattr(message, 'content', None) or "",
                        tool_calls=getattr(message, 'tool_calls', None),
                        model_used=model_name
                    )
                except Exception as retry_exc:
                    logger.error("Retry with tool_choice='auto' also failed: %s", retry_exc, exc_info=True)
                    raise Exception(f"Failed to call LLM with tools: {retry_exc}")
            
            logger.error("Error calling LLM with tools: %s", exc, exc_info=True)
            raise Exception(f"Failed to call LLM with tools: {exc}")
    
    async def call_with_rag_and_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        tools_schema: List[Dict],
        user_email: str,
        tool_choice: str = "auto",
        rag_client=None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Full integration: RAG + Tools."""
        if not data_sources:
            return await self.call_with_tools(model_name, messages, tools_schema, tool_choice, temperature=temperature)
        
        # Import RAG client if not provided
        if rag_client is None:
            from modules.rag import rag_client as default_rag_client
            rag_client = default_rag_client
        
        # Use the first selected data source
        data_source = data_sources[0]
        
        try:
            # Query RAG for context
            rag_response = await rag_client.query_rag(
                user_email,
                data_source,
                messages
            )
            
            # Integrate RAG context into messages
            messages_with_rag = messages.copy()
            rag_context_message = {
                "role": "system", 
                "content": f"Retrieved context from {data_source}:\n\n{rag_response.content}\n\nUse this context to inform your response."
            }
            messages_with_rag.insert(-1, rag_context_message)
            
            # Call LLM with enriched context and tools
            llm_response = await self.call_with_tools(model_name, messages_with_rag, tools_schema, tool_choice, temperature=temperature)
            
            # Append metadata to content if available and no tool calls
            if rag_response.metadata and not llm_response.has_tool_calls():
                metadata_summary = self._format_rag_metadata(rag_response.metadata)
                llm_response.content += f"\n\n---\n**RAG Sources & Processing Info:**\n{metadata_summary}"
            
            return llm_response
            
        except Exception as exc:
            logger.error(f"Error in RAG+tools integrated query: {exc}")
            # Fallback to tools-only call
            return await self.call_with_tools(model_name, messages, tools_schema, tool_choice, temperature=temperature)
    
    def _format_rag_metadata(self, metadata) -> str:
        """Format RAG metadata into a user-friendly summary."""
        # Import here to avoid circular imports
        try:
            from modules.rag.models import RAGMetadata
            if not isinstance(metadata, RAGMetadata):
                return "Metadata unavailable"
        except ImportError:
            return "Metadata unavailable"
        
        summary_parts = []
        summary_parts.append(f" **Data Source:** {metadata.data_source_name}")
        summary_parts.append(f" **Processing Time:** {metadata.query_processing_time_ms}ms")
        
        if metadata.documents_found:
            summary_parts.append(f" **Documents Found:** {len(metadata.documents_found)} (searched {metadata.total_documents_searched})")
            
            for i, doc in enumerate(metadata.documents_found[:3]):
                confidence_percent = int(doc.confidence_score * 100)
                summary_parts.append(f"  • {doc.source} ({confidence_percent}% relevance, {doc.content_type})")
            
            if len(metadata.documents_found) > 3:
                remaining = len(metadata.documents_found) - 3
                summary_parts.append(f"  • ... and {remaining} more document(s)")
        
        summary_parts.append(f" **Retrieval Method:** {metadata.retrieval_method}")
        return "\n".join(summary_parts)