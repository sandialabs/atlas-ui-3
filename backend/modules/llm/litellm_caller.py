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

import logging
import os
from typing import Any, Dict, List, Optional

import litellm
from litellm import acompletion
from .models import LLMResponse
from modules.config.config_manager import resolve_env_var
from core.metrics_logger import log_metric

logger = logging.getLogger(__name__)

# Configure LiteLLM settings
litellm.drop_params = True  # Drop unsupported params instead of erroring


class LiteLLMCaller:
    """Clean interface for all LLM calling patterns using LiteLLM.

    Note: this class may set provider-specific LLM API key environment
    variables (for example ``OPENAI_API_KEY``) to maintain compatibility
    with LiteLLM's internal provider detection. These mutations are
    best-effort only and are not intended to provide strong isolation
    guarantees in multi-tenant or highly concurrent environments.
    """
    
    def __init__(self, llm_config=None, debug_mode: bool = False, rag_service=None):
        """Initialize with optional config dependency injection.

        Args:
            llm_config: LLM configuration object
            debug_mode: Enable verbose LiteLLM logging (overridden by feature flag)
            rag_service: UnifiedRAGService for RAG-augmented calls
        """
        if llm_config is None:
            from modules.config import config_manager
            self.llm_config = config_manager.llm_config
        else:
            self.llm_config = llm_config

        # Store RAG service for RAG queries
        self._rag_service = rag_service

        # Set litellm verbosity based on debug mode, but respect the suppress feature flag
        # The feature flag takes precedence - if suppression is enabled, never set verbose
        from modules.config.config_manager import get_app_settings
        app_settings = get_app_settings()
        if app_settings.feature_suppress_litellm_logging:
            litellm.set_verbose = False
        else:
            litellm.set_verbose = debug_mode

    @staticmethod
    def _parse_qualified_data_source(qualified_data_source: str) -> str:
        """Extract corpus name from a qualified data source identifier.

        Qualified data sources have format "server:source_id" (e.g., "atlas_rag:technical-docs").
        The prefix is used for routing in multi-RAG setups, but the RAG API expects just
        the corpus name.

        Args:
            qualified_data_source: Data source ID, optionally prefixed with server name.

        Returns:
            The corpus/source name without the server prefix.
        """
        if ":" in qualified_data_source:
            _, data_source = qualified_data_source.split(":", 1)
            logger.debug("Stripped RAG server prefix: %s -> %s", qualified_data_source, data_source)
            return data_source
        return qualified_data_source

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
        elif "cerebras" in model_config.model_url:
            return f"cerebras/{model_id}"
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
        
        # Set API key - resolve environment variables
        try:
            api_key = resolve_env_var(model_config.api_key)
        except ValueError as e:
            logger.error(f"Failed to resolve API key for model {model_name}: {e}")
            raise

        if api_key:
            # Always pass api_key to LiteLLM for all providers
            kwargs["api_key"] = api_key

            # Additionally set provider-specific env vars for LiteLLM's internal logic
            def _set_env_var_if_needed(env_key: str, value: str) -> None:
                existing = os.environ.get(env_key)
                if existing is None:
                    os.environ[env_key] = value
                elif existing != value:
                    logger.warning(
                        "Overwriting existing environment variable %s for model %s", 
                        env_key,
                        model_name,
                    )
                    os.environ[env_key] = value

            if "openrouter" in model_config.model_url:
                _set_env_var_if_needed("OPENROUTER_API_KEY", api_key)
            elif "openai" in model_config.model_url:
                _set_env_var_if_needed("OPENAI_API_KEY", api_key)
            elif "anthropic" in model_config.model_url:
                _set_env_var_if_needed("ANTHROPIC_API_KEY", api_key)
            elif "google" in model_config.model_url:
                _set_env_var_if_needed("GOOGLE_API_KEY", api_key)
            elif "cerebras" in model_config.model_url:
                _set_env_var_if_needed("CEREBRAS_API_KEY", api_key)
            else:
                # Custom endpoint - set OPENAI_API_KEY as fallback for
                # OpenAI-compatible endpoints. This is a heuristic and
                # only updates the env var if it is unset or already
                # matches the same value.
                _set_env_var_if_needed("OPENAI_API_KEY", api_key)
        
        # Set custom API base for non-standard endpoints
        if hasattr(model_config, 'model_url') and model_config.model_url:
            if not any(provider in model_config.model_url for provider in ["openrouter", "api.openai.com", "api.anthropic.com", "api.cerebras.ai"]):
                kwargs["api_base"] = model_config.model_url
        
        # Handle extra headers with environment variable expansion
        if model_config.extra_headers:
            extra_headers_resolved = {}
            for header_key, header_value in model_config.extra_headers.items():
                try:
                    resolved_value = resolve_env_var(header_value)
                    extra_headers_resolved[header_key] = resolved_value
                except ValueError as e:
                    logger.error(f"Failed to resolve extra header '{header_key}' for model {model_name}: {e}")
                    raise
            kwargs["extra_headers"] = extra_headers_resolved
        
        return kwargs

    async def call_plain(
        self, 
        model_name: str, 
        messages: List[Dict[str, str]], 
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        user_email: Optional[str] = None
    ) -> str:
        """Plain LLM call - no tools, no RAG.
        
        Args:
            model_name: Name of the model to use
            messages: List of message dicts with 'role' and 'content'
            temperature: Optional temperature override (uses config default if None)
            max_tokens: Optional max_tokens override (uses config default if None)
            user_email: Optional user email for metrics logging
        """
        litellm_model = self._get_litellm_model_name(model_name)
        model_kwargs = self._get_model_kwargs(model_name, temperature)
        
        # Override max_tokens if provided
        if max_tokens is not None:
            model_kwargs["max_tokens"] = max_tokens
        
        try:
            total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
            logger.info(f"Plain LLM call: {len(messages)} messages, {total_chars} chars")
            
            response = await acompletion(
                model=litellm_model,
                messages=messages,
                **model_kwargs
            )
            
            content = response.choices[0].message.content or ""
            # Log response preview only at DEBUG level to avoid logging sensitive data
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"LLM response preview: '{content[:200]}{'...' if len(content) > 200 else ''}'")
            else:
                logger.info(f"LLM response length: {len(content)} chars")
            
            log_metric("llm_call", user_email, model=model_name, message_count=len(messages))
            
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
        rag_service=None,
        temperature: float = 0.7,
    ) -> str:
        """LLM call with RAG integration."""
        logger.debug(
            "[LLM+RAG] call_with_rag called: model=%s, data_sources=%s, user=%s, message_count=%d",
            model_name,
            data_sources,
            user_email,
            len(messages),
        )

        if not data_sources:
            logger.debug("[LLM+RAG] No data sources provided, falling back to plain LLM call")
            return await self.call_plain(model_name, messages, temperature=temperature, user_email=user_email)

        # Use provided service or instance service
        if rag_service is None:
            rag_service = self._rag_service
        if rag_service is None:
            logger.error("[LLM+RAG] RAG service not configured")
            raise ValueError("RAG service not configured")

        # Use the first selected data source (qualified format: server:source_id)
        qualified_data_source = data_sources[0]
        # Extract just the source name for display purposes
        display_source = self._parse_qualified_data_source(qualified_data_source)

        logger.info(
            "[LLM+RAG] Querying RAG: source=%s, user=%s",
            qualified_data_source,
            user_email,
        )

        try:
            # Query RAG for context via UnifiedRAGService
            logger.debug("[LLM+RAG] Calling rag_service.query_rag...")
            rag_response = await rag_service.query_rag(
                user_email,
                qualified_data_source,
                messages
            )

            logger.debug(
                "[LLM+RAG] RAG response received: content_length=%d, has_metadata=%s",
                len(rag_response.content) if rag_response.content else 0,
                rag_response.metadata is not None,
            )
            logger.debug(
                "[LLM+RAG] RAG content preview: %s...",
                rag_response.content[:300] if rag_response.content else "(empty)",
            )

            # Integrate RAG context into messages
            messages_with_rag = messages.copy()
            rag_context_message = {
                "role": "system",
                "content": f"Retrieved context from {display_source}:\n\n{rag_response.content}\n\nUse this context to inform your response."
            }
            messages_with_rag.insert(-1, rag_context_message)

            logger.debug("[LLM+RAG] Calling LLM with RAG-enriched context...")
            # Call LLM with enriched context
            llm_response = await self.call_plain(model_name, messages_with_rag, temperature=temperature, user_email=user_email)

            # Only append metadata if RAG actually provided useful content
            # Skip if: no content, empty content, or default fallback messages
            rag_content_useful = bool(
                rag_response.content
                and rag_response.content.strip()
                and rag_response.content not in (
                    "No response from RAG system.",
                    "No response from MCP RAG.",
                    "No matching vehicles found.",
                )
            )

            if rag_content_useful and rag_response.metadata:
                metadata_summary = self._format_rag_metadata(rag_response.metadata)
                # Only append if we have actual metadata (not just "Metadata unavailable")
                if metadata_summary and metadata_summary != "Metadata unavailable":
                    llm_response += f"\n\n---\n**RAG Sources & Processing Info:**\n{metadata_summary}"

            logger.info(
                "[LLM+RAG] RAG-integrated query complete: response_length=%d, rag_content_useful=%s",
                len(llm_response),
                rag_content_useful,
            )
            return llm_response

        except Exception as exc:
            logger.error("[LLM+RAG] Error in RAG-integrated query: %s", exc, exc_info=True)
            logger.warning("[LLM+RAG] Falling back to plain LLM call due to RAG error")
            return await self.call_plain(model_name, messages, temperature=temperature, user_email=user_email)
    
    async def call_with_tools(
        self,
        model_name: str,
        messages: List[Dict[str, str]],
        tools_schema: List[Dict],
        tool_choice: str = "auto",
        temperature: float = 0.7,
        user_email: Optional[str] = None
    ) -> LLMResponse:
        """LLM call with tool support using LiteLLM."""
        if not tools_schema:
            content = await self.call_plain(model_name, messages, temperature=temperature, user_email=user_email)
            return LLMResponse(content=content, model_used=model_name)

        litellm_model = self._get_litellm_model_name(model_name)
        model_kwargs = self._get_model_kwargs(model_name, temperature)
        
        # Handle tool_choice parameter - try "required" first, fallback to "auto" if unsupported
        final_tool_choice = tool_choice

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

            if tool_choice == "required" and not getattr(message, 'tool_calls', None):
                logger.error(f"LLM failed to return tool calls when tool_choice was 'required'. Full response: {response}")
                raise ValueError("LLM failed to return tool calls when tool_choice was 'required'.")

            tool_calls = getattr(message, 'tool_calls', None)
            tool_count = len(tool_calls) if tool_calls else 0
            log_metric("llm_call", user_email, model=model_name, message_count=len(messages), tool_count=tool_count)

            return LLMResponse(
                content=getattr(message, 'content', None) or "",
                tool_calls=tool_calls,
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
        rag_service=None,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Full integration: RAG + Tools."""
        logger.debug(
            "[LLM+RAG+Tools] call_with_rag_and_tools called: model=%s, data_sources=%s, user=%s, tools_count=%d",
            model_name,
            data_sources,
            user_email,
            len(tools_schema) if tools_schema else 0,
        )

        if not data_sources:
            logger.debug("[LLM+RAG+Tools] No data sources provided, falling back to tools-only call")
            return await self.call_with_tools(model_name, messages, tools_schema, tool_choice, temperature=temperature, user_email=user_email)

        # Use provided service or instance service
        if rag_service is None:
            rag_service = self._rag_service
        if rag_service is None:
            logger.error("[LLM+RAG+Tools] RAG service not configured")
            raise ValueError("RAG service not configured")

        # Use the first selected data source (qualified format: server:source_id)
        qualified_data_source = data_sources[0]
        # Extract just the source name for display purposes
        display_source = self._parse_qualified_data_source(qualified_data_source)

        logger.info(
            "[LLM+RAG+Tools] Querying RAG: source=%s, user=%s",
            qualified_data_source,
            user_email,
        )

        try:
            # Query RAG for context via UnifiedRAGService
            logger.debug("[LLM+RAG+Tools] Calling rag_service.query_rag...")
            rag_response = await rag_service.query_rag(
                user_email,
                qualified_data_source,
                messages
            )

            logger.debug(
                "[LLM+RAG+Tools] RAG response received: content_length=%d, has_metadata=%s",
                len(rag_response.content) if rag_response.content else 0,
                rag_response.metadata is not None,
            )
            logger.debug(
                "[LLM+RAG+Tools] RAG content preview: %s...",
                rag_response.content[:300] if rag_response.content else "(empty)",
            )

            # Integrate RAG context into messages
            messages_with_rag = messages.copy()
            rag_context_message = {
                "role": "system",
                "content": f"Retrieved context from {display_source}:\n\n{rag_response.content}\n\nUse this context to inform your response."
            }
            messages_with_rag.insert(-1, rag_context_message)

            logger.debug("[LLM+RAG+Tools] Calling LLM with RAG-enriched context and tools...")
            # Call LLM with enriched context and tools
            llm_response = await self.call_with_tools(model_name, messages_with_rag, tools_schema, tool_choice, temperature=temperature, user_email=user_email)

            # Only append metadata if RAG actually provided useful content
            # Skip if: no content, empty content, or default fallback messages
            rag_content_useful = bool(
                rag_response.content
                and rag_response.content.strip()
                and rag_response.content not in (
                    "No response from RAG system.",
                    "No response from MCP RAG.",
                    "No matching vehicles found.",
                )
            )

            # Append metadata to content if RAG was useful, has metadata, and no tool calls
            if rag_content_useful and rag_response.metadata and not llm_response.has_tool_calls():
                metadata_summary = self._format_rag_metadata(rag_response.metadata)
                # Only append if we have actual metadata (not just "Metadata unavailable")
                if metadata_summary and metadata_summary != "Metadata unavailable":
                    llm_response.content += f"\n\n---\n**RAG Sources & Processing Info:**\n{metadata_summary}"

            logger.info(
                "[LLM+RAG+Tools] RAG+tools query complete: response_length=%d, has_tool_calls=%s, rag_content_useful=%s",
                len(llm_response.content) if llm_response.content else 0,
                llm_response.has_tool_calls(),
                rag_content_useful,
            )
            return llm_response

        except Exception as exc:
            logger.error("[LLM+RAG+Tools] Error in RAG+tools integrated query: %s", exc, exc_info=True)
            logger.warning("[LLM+RAG+Tools] Falling back to tools-only call due to RAG error")
            return await self.call_with_tools(model_name, messages, tools_schema, tool_choice, temperature=temperature, user_email=user_email)
    
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
