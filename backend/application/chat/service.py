"""Chat service - core business logic for chat operations."""

import logging
import json
import asyncio
from typing import Any, Dict, List, Optional, Callable, Awaitable
from uuid import UUID

from domain.errors import SessionError, ValidationError
from domain.messages.models import (
    ConversationHistory,
    Message,
    MessageRole,
    MessageType,
    ToolCall,
    ToolResult
)
from domain.sessions.models import Session
from interfaces.llm import LLMProtocol, LLMResponse
from modules.config import ConfigManager
from modules.prompts.prompt_provider import PromptProvider
from interfaces.tools import ToolManagerProtocol
from interfaces.transport import ChatConnectionProtocol

# Import utilities
from .utilities import tool_utils, file_utils, notification_utils, error_utils
from .agent import AgentLoopProtocol, ReActAgentLoop, ThinkActAgentLoop
from .agent.protocols import AgentContext, AgentEvent
from core.prompt_risk import calculate_prompt_injection_risk, log_high_risk_event
from core.auth_utils import create_authorization_manager

logger = logging.getLogger(__name__)

# Type hint for the update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class ChatService:
    """
    Core chat service that orchestrates chat operations.
    Transport-agnostic, testable business logic.
    """
    
    def __init__(
        self,
        llm: LLMProtocol,
        tool_manager: Optional[ToolManagerProtocol] = None,
        connection: Optional[ChatConnectionProtocol] = None,
        config_manager: Optional[ConfigManager] = None,
        file_manager: Optional[Any] = None,
    agent_loop: Optional[AgentLoopProtocol] = None,
    ):
        """
        Initialize chat service with dependencies.
        
        Args:
            llm: LLM protocol implementation
            tool_manager: Optional tool manager
            connection: Optional connection for sending updates
            config_manager: Configuration manager
            file_manager: File manager for S3 operations
        """
        self.llm = llm
        self.tool_manager = tool_manager
        self.connection = connection
        self.sessions: Dict[UUID, Session] = {}
        self.config_manager = config_manager
        self.prompt_provider: Optional[PromptProvider] = (
            PromptProvider(self.config_manager) if self.config_manager else None
        )
        self.file_manager = file_manager
        # Agent loop DI (default to ReActAgentLoop). Allow override via config/env.
        if agent_loop is not None:
            self.agent_loop = agent_loop
        else:
            strategy = None
            try:
                if self.config_manager:
                    strategy = self.config_manager.app_settings.agent_loop_strategy
            except Exception:
                strategy = None
            strategy = (strategy or "react").lower()
            if strategy in ("think-act", "think_act", "thinkact"):
                self.agent_loop = ThinkActAgentLoop(
                    llm=self.llm,
                    tool_manager=self.tool_manager,
                    prompt_provider=self.prompt_provider,
                    connection=self.connection,
                )
            else:
                self.agent_loop = ReActAgentLoop(
                    llm=self.llm,
                    tool_manager=self.tool_manager,
                    prompt_provider=self.prompt_provider,
                    connection=self.connection,
                )

    async def create_session(
        self,
        session_id: UUID,
        user_email: Optional[str] = None
    ) -> Session:
        """Create a new chat session."""
        session = Session(id=session_id, user_email=user_email)
        self.sessions[session_id] = session
        logger.info(f"Created session {session_id} for user {user_email}")
        return session
    
    async def handle_chat_message(
        self,
        session_id: UUID,
        content: str,
        model: str,
        selected_tools: Optional[List[str]] = None,
    selected_prompts: Optional[List[str]] = None,
        selected_data_sources: Optional[List[str]] = None,
        only_rag: bool = False,
        tool_choice_required: bool = False,
        user_email: Optional[str] = None,
        agent_mode: bool = False,
        temperature: float = 0.7,
        update_callback: Optional[UpdateCallback] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Handle incoming chat message using utilities for clean separation.
        
        Returns:
            Response dictionary to send to client
        """
        # Log input arguments with content trimmed
        content_preview = content[:100] + "..." if len(content) > 100 else content
        sanitized_kwargs = error_utils.sanitize_kwargs_for_logging(kwargs)
        
        logger.info(
            f"handle_chat_message called - session_id: {session_id}, "
            f"content: '{content_preview}', model: {model}, "
            f"selected_tools: {selected_tools}, selected_prompts: {selected_prompts}, selected_data_sources: {selected_data_sources}, "
            f"only_rag: {only_rag}, tool_choice_required: {tool_choice_required}, "
            f"user_email: {user_email}, agent_mode: {agent_mode}, "
            f"kwargs: {sanitized_kwargs}"
        )

        # Get or create session
        session = self.sessions.get(session_id)
        if not session:
            session = await self.create_session(session_id, user_email)
        
        # Add user message to history
        user_message = Message(
            role=MessageRole.USER,
            content=content,
            metadata={"model": model}
        )
        session.history.add_message(user_message)
        session.update_timestamp()

        # Prompt-injection risk check on user input (observe + log medium/high)
        try:
            pi = calculate_prompt_injection_risk(content or "", mode="general")
            if pi.get("risk_level") in ("medium", "high"):
                log_high_risk_event(
                    source="user_input",
                    user=user_email,
                    content=content or "",
                    score=int(pi.get("score", 0)),
                    risk_level=str(pi.get("risk_level")),
                    triggers=list(pi.get("triggers", [])),
                    extra={"session_id": str(session_id)},
                )
        except Exception:
            logger.debug("Prompt risk check failed (user input)", exc_info=True)
        
        # Handle user file ingestion using utilities
        session.context = await file_utils.handle_session_files(
            session_context=session.context,
            user_email=user_email,
            files_map=kwargs.get("files"),
            file_manager=self.file_manager,
            update_callback=update_callback
        )

        try:
            # Get conversation history and add files manifest
            messages = session.history.get_messages_for_llm()

            # Inject MCP-provided system prompt override if any selected prompt is present.
            # We only apply the first valid prompt found in the provided list and prepend it
            # as the first message with role "system".
            try:
                if selected_prompts and self.tool_manager:
                    # Iterate in order; when found, fetch prompt content and inject
                    for key in selected_prompts:
                        if not isinstance(key, str) or "_" not in key:
                            continue
                        server, prompt_name = key.split("_", 1)
                        # Retrieve prompt from MCP
                        try:
                            prompt_obj = await self.tool_manager.get_prompt(server, prompt_name)
                            # Attempt to extract text content from FastMCP PromptMessage
                            prompt_text = None
                            if isinstance(prompt_obj, str):
                                prompt_text = prompt_obj
                            else:
                                # FastMCP PromptMessage-like: may have 'content' list with text entries
                                # Try common shapes safely.
                                if hasattr(prompt_obj, "content"):
                                    content_field = getattr(prompt_obj, "content")
                                    # content could be list of objects with 'text'
                                    if isinstance(content_field, list) and content_field:
                                        first = content_field[0]
                                        if hasattr(first, "text") and isinstance(first.text, str):
                                            prompt_text = first.text
                                # Fallback: string dump
                            if not prompt_text:
                                prompt_text = str(prompt_obj)

                            if prompt_text:
                                # Prepend as system message override
                                messages = [{"role": "system", "content": prompt_text}] + messages
                                logger.info(
                                    "Applied MCP system prompt override from %s:%s (len=%d)",
                                    server,
                                    prompt_name,
                                    len(prompt_text),
                                )
                                break  # apply only one
                        except Exception:
                            logger.debug("Failed retrieving MCP prompt %s", key, exc_info=True)
            except Exception:
                logger.debug("Prompt override injection skipped due to non-fatal error", exc_info=True)
            files_manifest = file_utils.build_files_manifest(session.context)
            if files_manifest:
                messages.append(files_manifest)
            
            # Route to appropriate execution mode
            if agent_mode:
                # Delegate to agent loop manager
                response = await self._handle_agent_mode_via_loop(
                    session=session,
                    model=model,
                    messages=messages,
                    selected_tools=selected_tools,
                    selected_data_sources=selected_data_sources,
                    max_steps=kwargs.get("agent_max_steps", 30),
                    update_callback=update_callback,
                    temperature=temperature,
                )
            elif selected_tools and not only_rag:
                # Enforce MCP tool ACLs: filter tools to authorized servers only
                if self.tool_manager:
                    try:
                        user = user_email or ""
                        # Prefer tool_manager's own authorization method if available
                        if hasattr(self.tool_manager, "get_authorized_servers"):
                            authorized_servers = self.tool_manager.get_authorized_servers(user, None)  # type: ignore[attr-defined]
                        else:
                            auth_mgr = create_authorization_manager()
                            servers_config = getattr(self.tool_manager, "servers_config", {})
                            authorized_servers = auth_mgr.filter_authorized_servers(
                                user,
                                servers_config,
                                getattr(self.tool_manager, "get_server_groups", lambda s: []),
                            )
                        # Filter tools by server prefix
                        filtered_tools: List[str] = []
                        for t in selected_tools or []:
                            if t == "canvas_canvas":
                                filtered_tools.append(t)
                                continue
                            if isinstance(t, str) and "_" in t:
                                server = t.split("_", 1)[0]
                                if server in authorized_servers:
                                    filtered_tools.append(t)
                        selected_tools = filtered_tools
                    except Exception:
                        logger.debug("Tool ACL filtering failed; proceeding with original selection", exc_info=True)
                response = await self._handle_tools_mode_with_utilities(
                    session, model, messages, selected_tools, selected_data_sources,
                    user_email, tool_choice_required, update_callback, temperature=temperature
                )
            elif selected_data_sources:
                response = await self._handle_rag_mode(
                    session, model, messages, selected_data_sources, user_email, temperature=temperature
                )
            else:
                response = await self._handle_plain_mode(session, model, messages, temperature=temperature)
            
            return response
            
        except Exception as e:
            return error_utils.handle_chat_message_error(e, "chat message handling")
            
    async def handle_reset_session(
        self,
        session_id: UUID,
        user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Handle session reset request from frontend."""
        # End the current session
        self.end_session(session_id)
        
        # Create a new session
        new_session = await self.create_session(session_id, user_email)
        
        logger.info(f"Reset session {session_id} for user {user_email}")
        
        return {
            "type": "session_reset",
            "session_id": str(session_id),
            "message": "New session created"
        }

    async def _handle_plain_mode(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Handle plain LLM call without tools or RAG."""
        response_content = await self.llm.call_plain(model, messages, temperature=temperature)

        # Add assistant message to history
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=response_content
        )
        session.history.add_message(assistant_message)

        # Send the chat response and mark completion
        if self.connection:
            await notification_utils.notify_chat_response(
                message=response_content,
                has_pending_tools=False,
                update_callback=self.connection.send_json
            )
            await notification_utils.notify_response_complete(self.connection.send_json)

        return notification_utils.create_chat_response(response_content)

    async def _handle_tools_mode_with_utilities(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, Any]],
        selected_tools: List[str],
        selected_data_sources: Optional[List[str]] = None,
        user_email: Optional[str] = None,
        tool_choice_required: bool = False,
        update_callback: Optional[UpdateCallback] = None,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Handle tools mode using utility helpers and stream updates.

        - Fetch tool schemas for selected tools
        - Call the LLM with tools (optionally with RAG)
        - If no tool calls, return content
        - If tool calls, execute them with UI streaming and synthesize final answer
        - Persist artifacts and update session context
        """
        # Resolve schemas
        tools_schema = await error_utils.safe_get_tools_schema(self.tool_manager, selected_tools)

        # Call LLM with tools (and RAG if provided)
        llm_response = await error_utils.safe_call_llm_with_tools(
            llm_caller=self.llm,
            model=model,
            messages=messages,
            tools_schema=tools_schema,
            data_sources=selected_data_sources,
            user_email=user_email,
            tool_choice=("required" if tool_choice_required else "auto"),
            temperature=temperature,
        )

        # No tool calls -> treat as plain content
        if not llm_response or not llm_response.has_tool_calls():
            content = llm_response.content if llm_response else ""
            assistant_message = Message(role=MessageRole.ASSISTANT, content=content)
            session.history.add_message(assistant_message)
            if self.connection:
                await notification_utils.notify_chat_response(
                    message=content,
                    has_pending_tools=False,
                    update_callback=self.connection.send_json,
                )
                await notification_utils.notify_response_complete(self.connection.send_json)
            return notification_utils.create_chat_response(content)

        # Execute tool workflow
        session_context = self._build_session_context(session)
        final_response, tool_results = await tool_utils.execute_tools_workflow(
            llm_response=llm_response,
            messages=messages,
            model=model,
            session_context=session_context,
            tool_manager=self.tool_manager,
            llm_caller=self.llm,
            prompt_provider=self.prompt_provider,
            update_callback=update_callback or (self.connection.send_json if self.connection else None),
        )

        # Update session with artifacts
        await self._update_session_from_tool_results(
            session,
            tool_results,
            update_callback or (self.connection.send_json if self.connection else None),
        )

        # Add final assistant message to history
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=final_response,
            metadata={
                "tools": selected_tools,
                **({"data_sources": selected_data_sources} if selected_data_sources else {}),
            },
        )
        session.history.add_message(assistant_message)

        # Emit final chat response
        if self.connection:
            await notification_utils.notify_chat_response(
                message=final_response,
                has_pending_tools=False,
                update_callback=self.connection.send_json,
            )
            await notification_utils.notify_response_complete(self.connection.send_json)

        return notification_utils.create_chat_response(final_response)

    # async def _handle_tools_mode_with_utilities(
    #     self,
    #     session: Session,
    #     model: str,
    #     messages: List[Dict[str, Any]],
    #     selected_tools: List[str],
    #     selected_data_sources: Optional[List[str]] = None,
    #     user_email: Optional[str] = None,
    #     tool_choice_required: bool = False,
    #     update_callback: Optional[UpdateCallback] = None,
    # ) -> Dict[str, Any]:
    #     """Handle tools mode using stateless utilities with streaming updates.

    #     - Retrieves tool schemas
    #     - Calls LLM with tools (and optional RAG)
    #     - Executes tool calls with UI streaming
    #     - Optionally synthesizes final answer
    #     - Updates session context with produced artifacts
    #     """
    #     # Resolve schema for selected tools
    #     tools_schema = await error_utils.safe_get_tools_schema(self.tool_manager, selected_tools)

    #     # Call LLM with tools (and RAG if provided)
    #     llm_response = await error_utils.safe_call_llm_with_tools(
    #         llm_caller=self.llm,
    #         model=model,
    #         messages=messages,
    #         tools_schema=tools_schema,
    #         data_sources=selected_data_sources,
    #         user_email=user_email,
    #         tool_choice=("required" if tool_choice_required else "auto"),
    #     )

    #     # If no tool calls, treat as plain response
    #     if not llm_response or not llm_response.has_tool_calls():
    #         content = llm_response.content if llm_response else ""
    #         assistant_message = Message(role=MessageRole.ASSISTANT, content=content)
    #         session.history.add_message(assistant_message)
    #         # Emit response to UI
    #         if self.connection:
    #             await notification_utils.notify_chat_response(
    #                 message=content,
    #                 has_pending_tools=False,
    #                 update_callback=self.connection.send_json,
    #             )
    #             await notification_utils.notify_response_complete(self.connection.send_json)
    #         return notification_utils.create_chat_response(content)

    #     # Execute tool workflow with streaming
    #     session_context = self._build_session_context(session)
    #     final_response, tool_results = await tool_utils.execute_tools_workflow(
    #         llm_response=llm_response,
    #         messages=messages,
    #         model=model,
    #         session_context=session_context,
    #         tool_manager=self.tool_manager,
    #         llm_caller=self.llm,
    #         prompt_provider=self.prompt_provider,
    #         update_callback=update_callback or (self.connection.send_json if self.connection else None),
    #     )

    #     # Ingest artifacts and update session context
    #     await self._update_session_from_tool_results(
    #         session,
    #         tool_results,
    #         update_callback or (self.connection.send_json if self.connection else None),
    #     )

    #     # Add assistant message to history with metadata
    #     assistant_message = Message(
    #         role=MessageRole.ASSISTANT,
    #         content=final_response,
    #         metadata={
    #             "tools": selected_tools,
    #             **({"data_sources": selected_data_sources} if selected_data_sources else {}),
    #         },
    #     )
    #     session.history.add_message(assistant_message)

    #     # Emit final response
    #     if self.connection:
    #         await notification_utils.notify_chat_response(
    #             message=final_response,
    #             has_pending_tools=False,
    #             update_callback=self.connection.send_json,
    #         )
    #         await notification_utils.notify_response_complete(self.connection.send_json)

    #     return notification_utils.create_chat_response(final_response)

    async def _handle_rag_mode(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, str]],
        data_sources: List[str],
        user_email: str,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Handle LLM call with RAG integration."""
        response_content = await self.llm.call_with_rag(
            model, messages, data_sources, user_email, temperature=temperature
        )

        # Add assistant message to history
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=response_content,
            metadata={"data_sources": data_sources}
        )
        session.history.add_message(assistant_message)

        return notification_utils.create_chat_response(response_content)

    async def _handle_agent_mode(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, str]],
        selected_tools: Optional[List[str]],
        data_sources: Optional[List[str]],
        max_steps: int,
        update_callback: Optional[UpdateCallback] = None,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Deprecated: legacy inline implementation preserved for reference. Use _handle_agent_mode_via_loop."""
        # Forward to new loop to maintain single code path; keep signature for compatibility
        return await self._handle_agent_mode_via_loop(
            session=session,
            model=model,
            messages=messages,
            selected_tools=selected_tools,
            selected_data_sources=data_sources,
            max_steps=max_steps,
            update_callback=update_callback,
            temperature=temperature,
        )

    async def _handle_agent_mode_via_loop(
        self,
        session: Session,
        model: str,
        messages: List[Dict[str, Any]],
        selected_tools: Optional[List[str]],
        selected_data_sources: Optional[List[str]],
        max_steps: int,
        update_callback: Optional[UpdateCallback] = None,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """Handle agent mode using the injected AgentLoopProtocol with event streaming.

        Translates AgentEvents to UI notifications and persists artifacts; appends final
        assistant message to history and returns a chat response.
        """
        # Build agent context
        agent_context = AgentContext(
            session_id=session.id,
            user_email=session.user_email,
            files=session.context.get("files", {}),
            history=session.history,
        )

        # Event handler: map AgentEvents to existing notification_utils APIs
        async def handle_event(evt: AgentEvent) -> None:
            et = evt.type
            p = evt.payload or {}
            # UI notifications (guard on connection)
            if et == "agent_start" and self.connection:
                await notification_utils.notify_agent_update(update_type="agent_start", connection=self.connection, max_steps=p.get("max_steps"))
            elif et == "agent_turn_start" and self.connection:
                await notification_utils.notify_agent_update(update_type="agent_turn_start", connection=self.connection, step=p.get("step"))
            elif et == "agent_reason" and self.connection:
                await notification_utils.notify_agent_update(update_type="agent_reason", connection=self.connection, message=p.get("message"), step=p.get("step"))
            elif et == "agent_request_input" and self.connection:
                await notification_utils.notify_agent_update(update_type="agent_request_input", connection=self.connection, question=p.get("question"), step=p.get("step"))
            elif et == "agent_tool_start" and self.connection:
                await notification_utils.notify_agent_update(update_type="tool_start", connection=self.connection, tool=p.get("tool"))
            elif et == "agent_tool_complete" and self.connection:
                await notification_utils.notify_agent_update(update_type="tool_complete", connection=self.connection, tool=p.get("tool"), result=p.get("result"))

            # Artifact ingestion should run regardless of connection
            if et == "agent_tool_results":
                # Ingest artifacts produced by tools and emit file/canvas updates
                results = p.get("results") or []
                if results:
                    await self._update_session_from_tool_results(
                        session,
                        results,
                        (self.connection.send_json if self.connection else None),
                    )
            elif et == "agent_observe" and self.connection:
                await notification_utils.notify_agent_update(update_type="agent_observe", connection=self.connection, message=p.get("message"), step=p.get("step"))
            elif et == "agent_completion" and self.connection:
                await notification_utils.notify_agent_update(update_type="agent_completion", connection=self.connection, steps=p.get("steps"))
            elif et == "agent_error" and self.connection:
                await notification_utils.notify_agent_update(update_type="agent_error", connection=self.connection, message=p.get("message"))

        # Run the loop
        result = await self.agent_loop.run(
            model=model,
            messages=messages,
            context=agent_context,
            selected_tools=selected_tools,
            data_sources=selected_data_sources,
            max_steps=max_steps,
            temperature=temperature,
            event_handler=handle_event,
        )

        # Append final message
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=result.final_answer,
            metadata={"agent_mode": True, "steps": result.steps},
        )
        session.history.add_message(assistant_message)

        # Completion update
        if self.connection:
            await notification_utils.notify_agent_update(update_type="agent_completion", connection=self.connection, steps=result.steps)

        return notification_utils.create_chat_response(result.final_answer)
        """Handle agent mode with strict Reason–Act–Observe loop and UI streaming.

        - Reason: plan next action with a dedicated prompt; emit agent_reason.
        - Act: run tool calls (if any) and stream tool_start/tool_complete.
        - Observe: reflect on tool outputs; decide to continue or finalize.
        - Supports stop control and optional user input pauses.
        """

        # Helper: extract latest user question
        def _latest_user_question(msgs: List[Dict[str, Any]]) -> str:
            for m in reversed(msgs):
                if m.get("role") == "user" and m.get("content"):
                    return str(m.get("content"))
            return ""

        # Helper: extract arguments for a named function tool call from an LLMResponse
        def _extract_tool_args(llm_response: LLMResponse, fname: str) -> Dict[str, Any]:
            try:
                if not llm_response or not llm_response.tool_calls:
                    return {}
                for tc in llm_response.tool_calls:
                    f = tc.get("function") if isinstance(tc, dict) else None
                    if f and f.get("name") == fname:
                        raw_args = f.get("arguments")
                        if isinstance(raw_args, str):
                            try:
                                return json.loads(raw_args)
                            except Exception:
                                return {}
                        if isinstance(raw_args, dict):
                            return raw_args
                return {}
            except Exception:
                return {}

        # Fallback: try parse a JSON object from a text block (last {...})
        def _parse_control_json(text: str) -> Dict[str, Any]:
            try:
                return json.loads(text)
            except Exception:
                pass
            if not isinstance(text, str):
                return {}
            start = text.rfind("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except Exception:
                    return {}
            return {}

        # Helper: opportunistically check for stop / user input messages
        async def _poll_control_message(timeout_sec: float = 0.01) -> Optional[Dict[str, Any]]:
            if not self.connection:
                return None
            try:
                return await asyncio.wait_for(self.connection.receive_json(), timeout=timeout_sec)
            except Exception:
                return None

        # Send agent start update
        if self.connection:
            await notification_utils.notify_agent_update(
                update_type="agent_start",
                connection=self.connection,
                max_steps=max_steps,
            )

        steps = 0
        final_response: Optional[str] = None
        last_observation: Optional[str] = None
        user_question = _latest_user_question(messages)
        files_manifest_obj = file_utils.build_files_manifest(self._build_session_context(session))
        files_manifest_text = files_manifest_obj.get("content") if files_manifest_obj else None

        while steps < max_steps:
            steps += 1

            # Send step update
            if self.connection:
                await notification_utils.notify_agent_update(
                    update_type="agent_turn_start",
                    connection=self.connection,
                    step=steps,
                )

            # Note: avoid non-deterministic polling here to prevent consuming
            # non-stop messages (like agent_user_input) prematurely.

            # ===== Reason (via synthetic tool call) =====
            reason_prompt = None
            if self.prompt_provider:
                reason_prompt = self.prompt_provider.get_agent_reason_prompt(
                    user_question=user_question,
                    files_manifest=files_manifest_text,
                    last_observation=last_observation,
                )
            reason_messages = list(messages)
            if reason_prompt:
                reason_messages.append({"role": "system", "content": reason_prompt})
            # Provide a single control tool the model must call
            reason_tools_schema: List[Dict[str, Any]] = [
                {
                    "type": "function",
                    "function": {
                        "name": "agent_decide_next",
                        "description": (
                            "Plan the next action. If you can answer now, set finish=true and "
                            "provide final_answer. If you need information from the user, set "
                            "request_input={question: "
                            '"..."}.'
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "finish": {"type": "boolean", "description": "Set true to finish now."},
                                "final_answer": {"type": "string", "description": "Final assistant answer when finishing."},
                                "request_input": {
                                    "type": "object",
                                    "properties": {
                                        "question": {"type": "string", "description": "Ask the user this question."}
                                    },
                                    "required": ["question"],
                                },
                                "next_plan": {"type": "string", "description": "Brief plan for the next step."},
                                "tools_to_consider": {"type": "array", "items": {"type": "string"}},
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            ]

            reason_resp: LLMResponse = await self.llm.call_with_tools(
                model, reason_messages, reason_tools_schema, "required", temperature=temperature
            )
            # Decide which content/control to use (prefer tool args, else fallback to plain)
            reason_ctrl = _extract_tool_args(reason_resp, "agent_decide_next") or _parse_control_json(reason_resp.content)
            reason_visible_text: str = reason_resp.content or ""
            if not reason_ctrl:
                # Fallback to plain model output that includes control JSON (for older tests/mocks)
                reason_text_fallback = await self.llm.call_plain(model, reason_messages, temperature=temperature)
                reason_visible_text = reason_text_fallback
                reason_ctrl = _parse_control_json(reason_text_fallback)

            if self.connection:
                await notification_utils.notify_agent_update(
                    update_type="agent_reason",
                    connection=self.connection,
                    message=reason_visible_text,
                    step=steps,
                )
            finish_flag = bool(reason_ctrl.get("finish")) if isinstance(reason_ctrl, dict) else False
            req_input = reason_ctrl.get("request_input") if isinstance(reason_ctrl, dict) else None
            if not req_input and isinstance(reason_visible_text, str) and '"request_input"' in reason_visible_text:
                try:
                    import re as _re
                    m = _re.search(r'"request_input"\s*:\s*\{[^}]*"question"\s*:\s*"([^"]+)"', reason_visible_text)
                    if m:
                        req_input = {"question": m.group(1)}
                except Exception:
                    pass

            # Optional: handle request for user input
            if req_input and isinstance(req_input, dict) and req_input.get("question") and self.connection:
                await notification_utils.notify_agent_update(
                    update_type="agent_request_input",
                    connection=self.connection,
                    question=str(req_input.get("question")),
                    step=steps,
                )
                # Wait for user response (with periodic stop checks)
                user_reply: Optional[str] = None
                for _ in range(600):  # ~60s with 0.1s polling
                    ctrl = await _poll_control_message(timeout_sec=0.1)
                    if ctrl and ctrl.get("type") == "agent_user_input" and ctrl.get("content"):
                        user_reply = str(ctrl.get("content"))
                        break
                    if ctrl and ctrl.get("type") == "agent_control" and ctrl.get("action") == "stop":
                        break
                if user_reply:
                    # Append as a new user message to continue loop
                    messages.append({"role": "user", "content": user_reply})
                    user_question = user_reply
                    last_observation = "User provided additional input."
                    continue
                # If no reply, stop gracefully
                break

            if finish_flag:
                final_response = reason_ctrl.get("final_answer") or reason_resp.content
                break

            # ===== Act =====
            tools_schema: List[Dict[str, Any]] = []
            if selected_tools and self.tool_manager:
                tools_schema = await error_utils.safe_get_tools_schema(self.tool_manager, selected_tools)

            tool_results: List[ToolResult] = []
            if tools_schema:
                # Request LLM to make tool calls using current conversation
                if data_sources and session.user_email:
                    llm_response = await self.llm.call_with_rag_and_tools(
                        model, messages, data_sources, tools_schema, session.user_email, "auto", temperature=temperature
                    )
                else:
                    llm_response = await self.llm.call_with_tools(
                        model, messages, tools_schema, "auto", temperature=temperature
                    )

                if llm_response.has_tool_calls():
                    session_context = self._build_session_context(session)
                    # Enforce single-tool execution per act step
                    first_call = (llm_response.tool_calls or [None])[0]
                    if first_call is None:
                        # Defensive fallback: no callable tool despite has_tool_calls
                        if llm_response.content:
                            final_response = llm_response.content
                            break
                    # Add assistant tool_calls message to transcript with only the first call
                    messages.append({
                        "role": "assistant",
                        "content": llm_response.content,
                        "tool_calls": [first_call],
                    })
                    # Execute only the first tool call
                    result = await tool_utils.execute_single_tool(
                        tool_call=first_call,
                        session_context=session_context,
                        tool_manager=self.tool_manager,
                        update_callback=update_callback or (self.connection.send_json if self.connection else None),
                    )
                    tool_results.append(result)
                    # Append tool result message
                    messages.append({
                        "role": "tool",
                        "content": result.content,
                        "tool_call_id": result.tool_call_id,
                    })

                    # Persist artifacts and emit file/canvas updates
                    await self._update_session_from_tool_results(
                        session,
                        tool_results,
                        update_callback or (self.connection.send_json if self.connection else None),
                    )
                else:
                    # No tool calls produced; fall back to plain response as final
                    if llm_response.content:
                        final_response = llm_response.content
                        break

            # ===== Observe (via synthetic tool call) =====
            # Build a concise summary of tool results for the observe prompt
            summaries: List[str] = []
            for tr in tool_results:
                try:
                    name = ""
                    # Try infer the name from trailing assistant tool_calls (best-effort)
                    if messages and isinstance(messages[-2] if len(messages) >= 2 else {}, dict):
                        tc_msg = messages[-2]
                        if tc_msg.get("role") == "assistant" and tc_msg.get("tool_calls"):
                            # Find the specific call by id
                            for tc in tc_msg.get("tool_calls", []) or []:
                                if tc.get("id") == tr.tool_call_id:
                                    name = tc.get("function", {}).get("name") or "tool"
                                    break
                    content_preview = (tr.content or "").strip()
                    if len(content_preview) > 400:
                        content_preview = content_preview[:400] + "..."
                    summaries.append(f"{name}: {content_preview}")
                except Exception:
                    summaries.append(str(getattr(tr, "content", "(no content)")))
            tool_summaries_text = "\n".join(summaries) if summaries else "No tools were executed."

            observe_prompt = None
            if self.prompt_provider:
                observe_prompt = self.prompt_provider.get_agent_observe_prompt(
                    user_question=user_question,
                    tool_summaries=tool_summaries_text,
                    step=steps,
                )
            observe_messages = list(messages)
            if observe_prompt:
                observe_messages.append({"role": "system", "content": observe_prompt})
            observe_tools_schema: List[Dict[str, Any]] = [
                {
                    "type": "function",
                    "function": {
                        "name": "agent_observe_decide",
                        "description": (
                            "Given the observations, decide whether to continue another step or finish."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "should_continue": {"type": "boolean", "description": "False to stop."},
                                "final_answer": {"type": "string", "description": "Provide final answer if stopping."},
                                "observation": {"type": "string", "description": "Brief observation/notes."},
                            },
                            "additionalProperties": False,
                        },
                    },
                }
            ]

            observe_resp: LLMResponse = await self.llm.call_with_tools(
                model, observe_messages, observe_tools_schema, "required", temperature=temperature
            )
            observe_ctrl = _extract_tool_args(observe_resp, "agent_observe_decide") or _parse_control_json(observe_resp.content)
            observe_visible_text: str = observe_resp.content or ""
            if not observe_ctrl:
                # Fallback to plain model output
                observe_text_fallback = await self.llm.call_plain(model, observe_messages, temperature=temperature)
                observe_visible_text = observe_text_fallback
                observe_ctrl = _parse_control_json(observe_text_fallback)

            if self.connection:
                await notification_utils.notify_agent_update(
                    update_type="agent_observe",
                    connection=self.connection,
                    message=observe_visible_text,
                    step=steps,
                )
            if isinstance(observe_ctrl, dict):
                final_candidate = observe_ctrl.get("final_answer")
                should_continue = observe_ctrl.get("should_continue", True)
                if final_candidate and isinstance(final_candidate, str) and final_candidate.strip():
                    final_response = final_candidate
                    break
                if not should_continue:
                    # Use the natural language portion as the answer
                    final_response = observe_visible_text
                    break

            # Prepare for next cycle
            last_observation = observe_visible_text

        if not final_response:
            # Reached max steps, get final response
            final_response = await self.llm.call_plain(model, messages, temperature=temperature)

        # Add to history
        assistant_message = Message(
            role=MessageRole.ASSISTANT,
            content=final_response,
            metadata={"agent_mode": True, "steps": steps},
        )
        session.history.add_message(assistant_message)

        # Send completion update
        if self.connection:
            await notification_utils.notify_agent_update(
                update_type="agent_completion",
                connection=self.connection,
                steps=steps,
            )

        return notification_utils.create_chat_response(final_response)

    async def handle_download_file(
        self,
        session_id: UUID,
        filename: str,
        user_email: Optional[str]
    ) -> Dict[str, Any]:
        """Download a file by original filename (within session context)."""
        session = self.sessions.get(session_id)
        if not session or not self.file_manager or not user_email:
            return {
                "type": MessageType.FILE_DOWNLOAD.value,
                "filename": filename,
                "error": "Session or file manager not available"
            }
        ref = session.context.get("files", {}).get(filename)
        if not ref:
            return {
                "type": MessageType.FILE_DOWNLOAD.value,
                "filename": filename,
                "error": "File not found in session"
            }
        try:
            content_b64 = await self.file_manager.get_file_content(
                user_email=user_email,
                filename=filename,
                s3_key=ref.get("key")
            )
            if not content_b64:
                return {
                    "type": MessageType.FILE_DOWNLOAD.value,
                    "filename": filename,
                    "error": "Unable to retrieve file content"
                }
            return {
                "type": MessageType.FILE_DOWNLOAD.value,
                "filename": filename,
                "content_base64": content_b64
            }
        except Exception as e:
            logger.error(f"Download failed for {filename}: {e}")
            return {
                "type": MessageType.FILE_DOWNLOAD.value,
                "filename": filename,
                "error": str(e)
            }
    
    def _build_session_context(self, session: Session) -> Dict[str, Any]:
        """Build session context for utilities."""
        return {
            "session_id": session.id,
            "user_email": session.user_email,
            "files": session.context.get("files", {}),
            **session.context
        }

    async def _update_session_from_tool_results(
        self,
        session: Session,
        tool_results: List[ToolResult],
        update_callback: Optional[UpdateCallback]
    ) -> None:
        """Persist tool artifacts, update session context, and notify UI for canvas."""
        if not tool_results:
            return

        if not self.file_manager:
            logger.info("No file_manager configured; skipping artifact ingestion")
            return

        # Build a working session context including user email
        session_context: Dict[str, Any] = self._build_session_context(session)

        try:
            for result in tool_results:
                # Ingest v2 artifacts and emit files_update + canvas_files (with display hints)
                session_context = await file_utils.process_tool_artifacts(
                    session_context=session_context,
                    tool_result=result,
                    file_manager=self.file_manager,
                    update_callback=update_callback
                )

            # Persist updated context back to the session
            session.context.update({k: v for k, v in session_context.items() if k != "session_id"})
        except Exception as e:
            logger.error(f"Failed to update session from tool results: {e}", exc_info=True)

    def get_session(self, session_id: UUID) -> Optional[Session]:
        """Get session by ID."""
        return self.sessions.get(session_id)
    
    def end_session(self, session_id: UUID) -> None:
        """End a session."""
        if session_id in self.sessions:
            self.sessions[session_id].active = False
            logger.info(f"Ended session {session_id}")
