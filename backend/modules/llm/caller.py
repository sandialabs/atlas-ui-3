# """
# LLM calling interface that handles all modes of LLM interaction.

# This module provides a clean interface for calling LLMs in different modes:
# - Plain LLM calls (no tools)
# - LLM calls with RAG integration
# - LLM calls with tool support
# - LLM calls with both RAG and tools
# """

# import asyncio
# import json
# import logging
# import os
# from typing import Any, Dict, List, Optional
# from dataclasses import dataclass

# import requests
# from .models import LLMResponse

# logger = logging.getLogger(__name__)


# class LLMCaller:
#     """Clean interface for all LLM calling patterns."""
    
#     def __init__(self, llm_config=None):
#         """Initialize with optional config dependency injection."""
#         if llm_config is None:
#             from modules.config import config_manager
#             self.llm_config = config_manager.llm_config
#         else:
#             self.llm_config = llm_config
    
#     async def call_plain(self, model_name: str, messages: List[Dict[str, str]]) -> str:
#         """Plain LLM call - no tools, no RAG."""
#         if model_name not in self.llm_config.models:
#             raise ValueError(f"Model {model_name} not found in configuration")

#         model_config = self.llm_config.models[model_name]
#         api_url = model_config.model_url
#         api_key = os.path.expandvars(model_config.api_key)
#         model_id = model_config.model_name

#         headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
#         # Inject any extra provider-specific headers (expand env vars, skip unresolved placeholders)
#         if getattr(model_config, 'extra_headers', None):
#             for h_key, h_val in model_config.extra_headers.items():
#                 if not h_val:
#                     continue
#                 expanded = os.path.expandvars(h_val)
#                 # Skip if still looks like an unresolved ${VAR}
#                 if expanded.startswith("${") and expanded.endswith("}"):
#                     continue
#                 headers[h_key] = expanded
#         payload = {"model": model_id, "messages": messages, "max_tokens": model_config.max_tokens, "temperature": 0.7}

#         VERBOSE = True

#         try:
#             total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
#             logger.info(f"Plain LLM call: {len(messages)} messages, {total_chars} chars")
          
#             # if verbose, just print the "message"   list
#             if VERBOSE: 
#                 logging.info(f"Messages are: {[m for m in messages]}")

#             loop = asyncio.get_event_loop()
#             response = await loop.run_in_executor(
#                 None, lambda: requests.post(api_url, headers=headers, json=payload, timeout=30)
#             )
            
#             if response.status_code == 200:
#                 result = response.json()
#                 llm_response = result["choices"][0]["message"]["content"]
#                 logger.info(f"LLM response preview: '{llm_response[:200]}{'...' if len(llm_response) > 200 else ''}'")
#                 return llm_response
            
#             logger.error("LLM API error %s: %s", response.status_code, response.text)
#             raise Exception(f"LLM API error: {response.status_code}")
            
#         except requests.RequestException as exc:
#             logger.error("Request error calling LLM: %s", exc, exc_info=True)
#             raise Exception(f"Failed to call LLM: {exc}")
#         except KeyError as exc:
#             logger.error("Invalid response format from LLM: %s", exc, exc_info=True)
#             raise Exception("Invalid response format from LLM")
    
#     async def call_with_rag(
#         self, 
#         model_name: str, 
#         messages: List[Dict[str, str]], 
#         data_sources: List[str],
#         user_email: str,
#         rag_client=None
#     ) -> str:
#         """LLM call with RAG integration."""
#         if not data_sources:
#             return await self.call_plain(model_name, messages)
        
#         # Import RAG client if not provided
#         if rag_client is None:
#             from modules.rag import rag_client as default_rag_client
#             rag_client = default_rag_client
        
#         # Use the first selected data source
#         data_source = data_sources[0]
        
#         try:
#             # Query RAG for context
#             rag_response = await rag_client.query_rag(
#                 user_email,
#                 data_source,
#                 messages
#             )
            
#             # Integrate RAG context into messages
#             messages_with_rag = messages.copy()
#             rag_context_message = {
#                 "role": "system", 
#                 "content": f"Retrieved context from {data_source}:\n\n{rag_response.content}\n\nUse this context to inform your response."
#             }
#             messages_with_rag.insert(-1, rag_context_message)
            
#             # Call LLM with enriched context
#             llm_response = await self.call_plain(model_name, messages_with_rag)
            
#             # Append metadata if available
#             if rag_response.metadata:
#                 metadata_summary = self._format_rag_metadata(rag_response.metadata)
#                 llm_response += f"\n\n---\n**RAG Sources & Processing Info:**\n{metadata_summary}"
            
#             return llm_response
            
#         except Exception as exc:
#             logger.error(f"Error in RAG-integrated query: {exc}")
#             # Fallback to plain LLM call
#             return await self.call_plain(model_name, messages)
    
#     async def call_with_tools(
#         self,
#         model_name: str,
#         messages: List[Dict[str, str]],
#         tools_schema: List[Dict],
#         tool_choice: str = "auto"
#     ) -> LLMResponse:
#         """LLM call with tool support."""
#         if not tools_schema:
#             content = await self.call_plain(model_name, messages)
#             return LLMResponse(content=content, model_used=model_name)

#         if model_name not in self.llm_config.models:
#             raise ValueError(f"Model {model_name} not found in configuration")

#         model_config = self.llm_config.models[model_name]
#         api_url = model_config.model_url
#         api_key = os.path.expandvars(model_config.api_key)
#         model_id = model_config.model_name

#         headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
#         if getattr(model_config, 'extra_headers', None):
#             for h_key, h_val in model_config.extra_headers.items():
#                 if not h_val:
#                     continue
#                 expanded = os.path.expandvars(h_val)
#                 if expanded.startswith("${") and expanded.endswith("}"):
#                     continue
#                 headers[h_key] = expanded
#         payload = {
#             "model": model_id,
#             "messages": messages,
#             "tools": tools_schema,
#             "tool_choice": tool_choice,
#             "max_tokens": model_config.max_tokens,
#             "temperature": 0.7,
#         }

#         try:
#             total_chars = sum(len(str(msg.get('content', ''))) for msg in messages)
#             logger.info(f"LLM call with tools: {len(messages)} messages, {total_chars} chars, {len(tools_schema)} tools")
            
#             loop = asyncio.get_event_loop()
#             response = await loop.run_in_executor(
#                 None, lambda: requests.post(api_url, headers=headers, json=payload, timeout=30)
#             )
            
#             if response.status_code != 200:
#                 logger.error("LLM API error %s: %s", response.status_code, response.text)
#                 raise Exception(f"LLM API error: {response.status_code}")

#             result = response.json()
#             choice = result["choices"][0]
#             message = choice["message"]
            
#             return LLMResponse(
#                 content=message.get("content", ""),
#                 tool_calls=message.get("tool_calls"),
#                 model_used=model_name
#             )
            
#         except requests.RequestException as exc:
#             logger.error("Request error calling LLM with tools: %s", exc, exc_info=True)
#             raise Exception(f"Failed to call LLM: {exc}")
#         except KeyError as exc:
#             logger.error("Invalid response format from LLM: %s", exc, exc_info=True)
#             raise Exception("Invalid response format from LLM")
    
#     async def call_with_rag_and_tools(
#         self,
#         model_name: str,
#         messages: List[Dict[str, str]],
#         data_sources: List[str],
#         tools_schema: List[Dict],
#         user_email: str,
#         tool_choice: str = "auto",
#         rag_client=None
#     ) -> LLMResponse:
#         """Full integration: RAG + Tools."""
#         if not data_sources:
#             return await self.call_with_tools(model_name, messages, tools_schema, tool_choice)
        
#         # Import RAG client if not provided
#         if rag_client is None:
#             from modules.rag import rag_client as default_rag_client
#             rag_client = default_rag_client
        
#         # Use the first selected data source
#         data_source = data_sources[0]
        
#         try:
#             # Query RAG for context
#             rag_response = await rag_client.query_rag(
#                 user_email,
#                 data_source,
#                 messages
#             )
            
#             # Integrate RAG context into messages
#             messages_with_rag = messages.copy()
#             rag_context_message = {
#                 "role": "system", 
#                 "content": f"Retrieved context from {data_source}:\n\n{rag_response.content}\n\nUse this context to inform your response."
#             }
#             messages_with_rag.insert(-1, rag_context_message)
            
#             # Call LLM with enriched context and tools
#             llm_response = await self.call_with_tools(model_name, messages_with_rag, tools_schema, tool_choice)
            
#             # Append metadata to content if available and no tool calls
#             if rag_response.metadata and not llm_response.has_tool_calls():
#                 metadata_summary = self._format_rag_metadata(rag_response.metadata)
#                 llm_response.content += f"\n\n---\n**RAG Sources & Processing Info:**\n{metadata_summary}"
            
#             return llm_response
            
#         except Exception as exc:
#             logger.error(f"Error in RAG+tools integrated query: {exc}")
#             # Fallback to tools-only call
#             return await self.call_with_tools(model_name, messages, tools_schema, tool_choice)
    
#     def _format_rag_metadata(self, metadata) -> str:
#         """Format RAG metadata into a user-friendly summary."""
#         # Import here to avoid circular imports
#         try:
#             from modules.rag.models import RAGMetadata
#             if not isinstance(metadata, RAGMetadata):
#                 return "Metadata unavailable"
#         except ImportError:
#             return "Metadata unavailable"
        
#         summary_parts = []
#         summary_parts.append(f" **Data Source:** {metadata.data_source_name}")
#         summary_parts.append(f" **Processing Time:** {metadata.query_processing_time_ms}ms")
        
#         if metadata.documents_found:
#             summary_parts.append(f" **Documents Found:** {len(metadata.documents_found)} (searched {metadata.total_documents_searched})")
            
#             for i, doc in enumerate(metadata.documents_found[:3]):
#                 confidence_percent = int(doc.confidence_score * 100)
#                 summary_parts.append(f"  • {doc.source} ({confidence_percent}% relevance, {doc.content_type})")
            
#             if len(metadata.documents_found) > 3:
#                 remaining = len(metadata.documents_found) - 3
#                 summary_parts.append(f"  • ... and {remaining} more document(s)")
        
#         summary_parts.append(f" **Retrieval Method:** {metadata.retrieval_method}")
#         return "\n".join(summary_parts)