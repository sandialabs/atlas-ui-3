"""Configuration API routes."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends

from atlas.core.auth import is_user_in_group
from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging
from atlas.infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])

# Canvas tool description constant
CANVAS_TOOL_DESCRIPTION = (
    "Display final rendered content in a visual canvas panel. "
    "Use this for: 1) Complete code (not code discussions), "
    "2) Final reports/documents (not report discussions), "
    "3) Data visualizations, 4) Any polished content that should be "
    "viewed separately from the conversation."
)


@router.get("/banners")
async def get_banners(current_user: str = Depends(get_current_user)):
    """Get banners for the user."""
    config_manager = app_factory.get_config_manager()
    app_settings = config_manager.app_settings

    # Check if banners are enabled
    if not app_settings.banner_enabled:
        return {"messages": []}

    # Read messages from messages.txt file
    try:
        from pathlib import Path

        # Use app settings for config path
        base = Path(app_settings.app_config_dir)

        # If relative path, resolve from project root
        if not base.is_absolute():
            project_root = Path(__file__).parent.parent.parent
            base = project_root / base

        messages_file = base / app_settings.messages_config_file

        if messages_file.exists():
            with open(messages_file, "r", encoding="utf-8") as f:
                content = f.read()
            messages = [line.strip() for line in content.splitlines() if line.strip()]
            return {"messages": messages}
        else:
            return {"messages": []}
    except Exception as e:
        logger.error(f"Error reading banner messages: {e}")
        return {"messages": []}


@router.get("/config")
async def get_config(
    current_user: str = Depends(get_current_user),
    compliance_level: Optional[str] = None,
):
    """Get available models, tools, and data sources for the user.
    Only returns MCP servers and tools that the user is authorized to access.
    """
    config_manager = app_factory.get_config_manager()
    llm_config = config_manager.llm_config
    app_settings = config_manager.app_settings

    # Get RAG data sources for the user from unified RAG service
    rag_data_sources = []
    rag_servers = []
    # Only attempt RAG discovery if RAG feature is enabled
    if app_settings.feature_rag_enabled:
        # Discover HTTP and MCP RAG sources independently (best-effort)
        # so a failure in one type does not prevent discovery of the other
        try:
            unified_rag = app_factory.get_unified_rag_service()
            if unified_rag:
                http_rag_servers = await unified_rag.discover_data_sources(
                    current_user, user_compliance_level=compliance_level
                )
                rag_servers.extend(http_rag_servers)
        except Exception as e:
            logger.warning("Error discovering HTTP RAG sources: %s", e)

        try:
            rag_mcp = app_factory.get_rag_mcp_service()
            if rag_mcp:
                mcp_rag_servers = await rag_mcp.discover_servers(
                    current_user, user_compliance_level=compliance_level
                )
                rag_servers.extend(mcp_rag_servers)
        except Exception as e:
            logger.warning("Error discovering MCP RAG sources: %s", e)

        # Build flat list of data sources for backward compatibility
        # Format: "server:source_id" for qualified references
        for server in rag_servers:
            server_name = server.get("server", "")
            for source in server.get("sources", []):
                source_id = source.get("id", "")
                if server_name and source_id:
                    rag_data_sources.append(f"{server_name}:{source_id}")

    # Check if tools are enabled
    tools_info = []
    prompts_info = []
    authorized_servers = []

    if app_settings.feature_tools_enabled:
        # Get MCP manager
        mcp_manager = app_factory.get_mcp_manager()

        # Get authorized servers for the user - this filters out unauthorized servers completely
        authorized_servers = await mcp_manager.get_authorized_servers(current_user, is_user_in_group)

        # Add canvas pseudo-tool to authorized servers (available to all users)
        authorized_servers.append("canvas")

        # Only build tool information for servers the user is authorized to access
        for server_name in authorized_servers:
            # Handle canvas pseudo-tool
            if server_name == "canvas":
                tools_info.append({
                    'server': 'canvas',
                    'tools': ['canvas'],
                    'tools_detailed': [{
                        'name': 'canvas',
                        'description': CANVAS_TOOL_DESCRIPTION,
                        'inputSchema': {
                            'type': 'object',
                            'properties': {
                                'content': {
                                    'type': 'string',
                                    'description': 'The content to display in the canvas. Can be markdown, code, or plain text.'
                                }
                            },
                            'required': ['content']
                        }
                    }],
                    'tool_count': 1,
                    'description': 'Canvas for showing final rendered content: complete code, reports, and polished documents. Use this to finalize your work. Most code and reports will be shown here.',
                    'author': 'Chat UI Team',
                    'short_description': 'Visual content display',
                    'help_email': 'support@chatui.example.com',
                    'compliance_level': 'Public'
                })
            elif server_name in mcp_manager.available_tools:
                server_tools = mcp_manager.available_tools[server_name]['tools']
                server_config = mcp_manager.available_tools[server_name]['config']

                # Only include servers that have tools and user has access to
                if server_tools:  # Only show servers with actual tools
                    # Build detailed tool information including descriptions and input schemas
                    tools_detailed = []
                    for tool in server_tools:
                        tool_detail = {
                            'name': tool.name,
                            'description': tool.description or '',
                            'inputSchema': getattr(tool, 'inputSchema', {}) or {}
                        }
                        tools_detailed.append(tool_detail)

                    # Determine auth_type from server config
                    auth_type = server_config.get('auth_type', 'none')
                    auth_required = auth_type in ('jwt', 'bearer', 'oauth', 'api_key')

                    tools_info.append({
                        'server': server_name,
                        'tools': [tool.name for tool in server_tools],
                        'tools_detailed': tools_detailed,
                        'tool_count': len(server_tools),
                        'description': server_config.get('description', f'{server_name} tools'),
                        'author': server_config.get('author', 'Unknown'),
                        'short_description': server_config.get('short_description', server_config.get('description', f'{server_name} tools')),
                        'help_email': server_config.get('help_email', ''),
                        'compliance_level': server_config.get('compliance_level'),
                        'auth_type': auth_type,
                        'auth_required': auth_required
                    })

            # Collect prompts from this server if available
            if server_name in mcp_manager.available_prompts:
                server_prompts = mcp_manager.available_prompts[server_name]['prompts']
                server_config = mcp_manager.available_prompts[server_name]['config']
                if server_prompts:  # Only show servers with actual prompts
                    prompts_info.append({
                        'server': server_name,
                        'prompts': [{'name': prompt.name, 'description': prompt.description} for prompt in server_prompts],
                        'prompt_count': len(server_prompts),
                        'description': f'{server_name} custom prompts',
                        'author': server_config.get('author', 'Unknown'),
                        'short_description': server_config.get('short_description', f'{server_name} custom prompts'),
                        'help_email': server_config.get('help_email', ''),
                        'compliance_level': server_config.get('compliance_level')
                    })

    # Read help page configuration (supports new config directory layout + legacy paths)
    help_config = {}
    import json
    help_config_filename = config_manager.app_settings.help_config_file
    help_paths = []
    try:
        # Reuse config manager search logic (private but acceptable for now)
        try:
            help_paths = config_manager._search_paths(help_config_filename)  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback minimal search if method renamed/removed
            from pathlib import Path
            atlas_root = Path(__file__).parent.parent
            project_root = atlas_root.parent
            help_paths = [
                project_root / "config" / "overrides" / help_config_filename,
                project_root / "config" / "defaults" / help_config_filename,
                atlas_root / "configfilesadmin" / help_config_filename,
                atlas_root / "configfiles" / help_config_filename,
                atlas_root / help_config_filename,
                project_root / help_config_filename,
            ]

        found_path = None
        for p in help_paths:
            if p.exists():
                found_path = p
                break
        if found_path:
            with open(found_path, "r", encoding="utf-8") as f:
                help_config = json.load(f)
            logger.info(f"Loaded help config from {found_path}")
        else:
            logger.warning(
                "Help config not found in any of these locations: %s",
                [str(p) for p in help_paths]
            )
            help_config = {"title": "Help & Documentation", "sections": []}
    except Exception as e:
        logger.warning(f"Error loading help config: {e}")
        help_config = {"title": "Help & Documentation", "sections": []}

    # Keep INFO logging concise; server lists can be very long.
    logger.info(
        "Config for user %s: %d authorized servers, %d tool groups",
        sanitize_for_logging(current_user),
        len(authorized_servers),
        len(tools_info),
    )
    logger.debug(
        "Authorized servers for user %s: %s",
        sanitize_for_logging(current_user),
        authorized_servers,
    )
    # Build models list with compliance levels and api_key_source
    from atlas.modules.mcp_tools.token_storage import get_token_storage
    token_storage = get_token_storage()

    models_list = []
    for model_name, model_config in llm_config.models.items():
        model_info = {
            "name": model_name,
            "description": model_config.description,
        }
        # Include compliance_level if feature is enabled
        if app_settings.feature_compliance_levels_enabled and model_config.compliance_level:
            model_info["compliance_level"] = model_config.compliance_level
        # Include api_key_source so frontend knows which models need user keys
        api_key_source = getattr(model_config, "api_key_source", "system")
        if api_key_source == "user":
            model_info["api_key_source"] = "user"
            stored = token_storage.get_valid_token(current_user, f"llm:{model_name}")
            model_info["user_has_key"] = stored is not None
        elif api_key_source == "globus":
            model_info["api_key_source"] = "globus"
            globus_scope = getattr(model_config, "globus_scope", None)
            model_info["globus_scope"] = globus_scope
            if globus_scope:
                stored = token_storage.get_valid_token(current_user, f"globus:{globus_scope}")
                model_info["user_has_key"] = stored is not None
            else:
                model_info["user_has_key"] = False
        models_list.append(model_info)

    # Build tool approval settings - only include tools from authorized servers
    tool_approvals_config = config_manager.tool_approvals_config
    filtered_tool_approvals = {}

    # Get all tool names from authorized servers
    authorized_tool_names = set()
    for tool_group in tools_info:
        server_name = tool_group.get('server')
        if server_name in authorized_servers:
            # tools is a list of strings (tool names), not dicts
            for tool_name in tool_group.get('tools', []):
                if isinstance(tool_name, str):
                    authorized_tool_names.add(tool_name)

    # Only include approval settings for tools the user has access to
    for tool_name, approval_config in tool_approvals_config.tools.items():
        if tool_name in authorized_tool_names:
            filtered_tool_approvals[tool_name] = {
                "require_approval": approval_config.require_approval,
                "allow_edit": approval_config.allow_edit
            }

    return {
        "app_name": app_settings.app_name,
        "models": models_list,
        "tools": tools_info,  # Only authorized servers are included
        "prompts": prompts_info,  # Available prompts from authorized servers
        "data_sources": rag_data_sources,  # RAG data sources for the user
        "rag_servers": rag_servers,  # Optional richer structure for RAG UI
        "user": current_user,
    "is_in_admin_group": await is_user_in_group(current_user, app_settings.admin_group),
        "active_sessions": 0,  # TODO: Implement session counting in ChatService
        "authorized_servers": authorized_servers,  # Optional: expose for debugging
        "agent_mode_available": app_settings.agent_mode_available,  # Whether agent mode UI should be shown
        "banner_enabled": app_settings.banner_enabled,  # Whether banner system is enabled
        "help_config": help_config,  # Help page configuration from help-config.json
        "tool_approvals": {
            "require_approval_by_default": tool_approvals_config.require_approval_by_default,
            "tools": filtered_tool_approvals
        },
        "features": {
            "workspaces": app_settings.feature_workspaces_enabled,
            "rag": app_settings.feature_rag_enabled,
            "tools": app_settings.feature_tools_enabled,
            "marketplace": app_settings.feature_marketplace_enabled,
            "files_panel": app_settings.feature_files_panel_enabled,
            "chat_history": app_settings.feature_chat_history_enabled,
            "chat_history_storage": _get_chat_history_storage_label(app_settings) if app_settings.feature_chat_history_enabled else None,
            "chat_history_save_modes": ["none", "local", "server"] if app_settings.feature_chat_history_enabled else [],
            "compliance_levels": app_settings.feature_compliance_levels_enabled,
            "splash_screen": app_settings.feature_splash_screen_enabled,
            "file_content_extraction": app_settings.feature_file_content_extraction_enabled,
            "globus_auth": app_settings.feature_globus_auth_enabled
        },
        "file_extraction": _get_file_extraction_config(config_manager)
    }


def _get_chat_history_storage_label(app_settings) -> str:
    """Return a human-readable label for the chat history storage backend."""
    db_url = app_settings.chat_history_db_url
    if not db_url:
        return "Local"
    if db_url.startswith("duckdb"):
        # Extract path from duckdb:///path/to/file.db
        path = db_url.split("///", 1)[-1] if "///" in db_url else db_url
        return f"DuckDB ({path})"
    if "postgresql" in db_url or "postgres" in db_url:
        return "PostgreSQL"
    if "sqlite" in db_url:
        path = db_url.split("///", 1)[-1] if "///" in db_url else db_url
        return f"SQLite ({path})"
    return "Database"


def _get_file_extraction_config(config_manager) -> dict:
    """Build file extraction config for frontend."""
    app_settings = config_manager.app_settings

    if not app_settings.feature_file_content_extraction_enabled:
        return {
            "enabled": False,
            "default_behavior": "none",
            "supported_extensions": []
        }

    try:
        extractors_config = config_manager.file_extractors_config

        # Get list of extensions with enabled extractors
        supported_extensions = []
        for ext, extractor_name in extractors_config.extension_mapping.items():
            extractor = extractors_config.extractors.get(extractor_name)
            if extractor and extractor.enabled:
                supported_extensions.append(ext)

        return {
            "enabled": extractors_config.enabled,
            "default_behavior": extractors_config.default_behavior,
            "supported_extensions": sorted(supported_extensions)
        }
    except Exception as e:
        logger.warning(f"Error building file extraction config: {e}")
        return {
            "enabled": False,
            "default_behavior": "none",
            "supported_extensions": []
        }


@router.get("/compliance-levels")
async def get_compliance_levels(current_user: str = Depends(get_current_user)):
    """Get compliance level definitions and allowlist."""
    try:
        from atlas.core.compliance import get_compliance_manager
        compliance_mgr = get_compliance_manager()

        # Return level definitions for frontend use
        levels = []
        for name, level_obj in compliance_mgr.levels.items():
            levels.append({
                "name": name,
                "description": level_obj.description,
                "aliases": level_obj.aliases,
                "allowed_with": level_obj.allowed_with
            })

        return {
            "levels": levels,
            "mode": compliance_mgr.mode,
            "all_level_names": compliance_mgr.get_all_levels()
        }
    except Exception as e:
        logger.error(f"Error getting compliance levels: {e}", exc_info=True)
        return {
            "levels": [],
            "mode": "explicit_allowlist",
            "all_level_names": []
        }


@router.get("/splash")
async def get_splash_config(current_user: str = Depends(get_current_user)):
    """Get splash screen configuration."""
    config_manager = app_factory.get_config_manager()
    app_settings = config_manager.app_settings

    # Check if splash screen feature is enabled
    if not app_settings.feature_splash_screen_enabled:
        return {
            "enabled": False,
            "title": "",
            "messages": [],
            "dismissible": True,
            "require_accept": False,
            "dismiss_duration_days": 30,
            "accept_button_text": "Accept",
            "dismiss_button_text": "Dismiss",
            "show_on_every_visit": False
        }

    # Read splash screen configuration
    splash_config = {}
    import json
    splash_config_filename = app_settings.splash_config_file
    splash_paths = []
    try:
        # Reuse config manager search logic
        try:
            splash_paths = config_manager._search_paths(splash_config_filename)  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback minimal search if method renamed/removed
            from pathlib import Path
            atlas_root = Path(__file__).parent.parent
            project_root = atlas_root.parent
            splash_paths = [
                project_root / "config" / "overrides" / splash_config_filename,
                project_root / "config" / "defaults" / splash_config_filename,
                atlas_root / "configfilesadmin" / splash_config_filename,
                atlas_root / "configfiles" / splash_config_filename,
                atlas_root / splash_config_filename,
                project_root / splash_config_filename,
            ]

        found_path = None
        for p in splash_paths:
            if p.exists():
                found_path = p
                break
        if found_path:
            with open(found_path, "r", encoding="utf-8") as f:
                splash_config = json.load(f)
            logger.info(f"Loaded splash config from {found_path}")
        else:
            logger.info(
                "Splash config not found in any of these locations: %s",
                [str(p) for p in splash_paths]
            )
            # Return default disabled config
            splash_config = {
                "enabled": False,
                "title": "",
                "messages": [],
                "dismissible": True,
                "require_accept": False,
                "dismiss_duration_days": 30,
                "accept_button_text": "Accept",
                "dismiss_button_text": "Dismiss",
                "show_on_every_visit": False
            }
    except Exception as e:
        logger.warning(f"Error loading splash config: {e}")
        splash_config = {
            "enabled": False,
            "title": "",
            "messages": [],
            "dismissible": True,
            "require_accept": False,
            "dismiss_duration_days": 30,
            "accept_button_text": "Accept",
            "dismiss_button_text": "Dismiss",
            "show_on_every_visit": False
        }

    return splash_config


# @router.get("/sessions")
# async def get_session_info(current_user: str = Depends(get_current_user)):
#     """Get session information for the current user."""
#     # TODO: Implement session info retrieval from ChatService
#     return {
#         "total_sessions": 0,
#         "user_sessions": 0,
#         "sessions": []
#     }
