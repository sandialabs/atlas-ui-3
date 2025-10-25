"""Configuration API routes."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends

from core.auth import is_user_in_group
from core.utils import get_current_user
from infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["config"])


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
        import os
        
        # Use same logic as admin routes to find messages file
        overrides_env = os.getenv("APP_CONFIG_OVERRIDES", "config/overrides")
        base = Path(overrides_env)
        
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
async def get_config(current_user: str = Depends(get_current_user)):
    """Get available models, tools, and data sources for the user.
    Only returns MCP servers and tools that the user is authorized to access.
    """
    config_manager = app_factory.get_config_manager()
    llm_config = config_manager.llm_config
    app_settings = config_manager.app_settings
    
    # Get RAG data sources for the user (feature-gated MCP-backed discovery)
    rag_data_sources = []
    rag_servers = []
    try:
        if app_settings.feature_rag_mcp_enabled:
            rag_mcp = app_factory.get_rag_mcp_service()
            rag_data_sources = await rag_mcp.discover_data_sources(current_user)
            rag_servers = await rag_mcp.discover_servers(current_user)
        else:
            rag_client = app_factory.get_rag_client()
            rag_data_sources = await rag_client.discover_data_sources(current_user)
    except Exception as e:
        logger.warning(f"Error resolving RAG data sources: {e}")
    
    # Check if tools are enabled
    tools_info = []
    prompts_info = []
    authorized_servers = []
    
    if app_settings.feature_tools_enabled:
        # Get MCP manager
        mcp_manager = app_factory.get_mcp_manager()
        
        # Get authorized servers for the user - this filters out unauthorized servers completely
        authorized_servers = mcp_manager.get_authorized_servers(current_user, is_user_in_group)
        
        # Add canvas pseudo-tool to authorized servers (available to all users)
        authorized_servers.append("canvas")
        
        # Only build tool information for servers the user is authorized to access
        for server_name in authorized_servers:
            # Handle canvas pseudo-tool
            if server_name == "canvas":
                tools_info.append({
                    'server': 'canvas',
                    'tools': ['canvas'],
                    'tool_count': 1,
                    'description': 'Canvas for showing final rendered content: complete code, reports, and polished documents. Use this to finalize your work. Most code and reports will be shown here.',
                    'is_exclusive': False,
                    'author': 'Chat UI Team',
                    'short_description': 'Visual content display',
                    'help_email': 'support@chatui.example.com'
                })
            elif server_name in mcp_manager.available_tools:
                server_tools = mcp_manager.available_tools[server_name]['tools']
                server_config = mcp_manager.available_tools[server_name]['config']
                
                # Only include servers that have tools and user has access to
                if server_tools:  # Only show servers with actual tools
                    tools_info.append({
                        'server': server_name,
                        'tools': [tool.name for tool in server_tools],
                        'tool_count': len(server_tools),
                        'description': server_config.get('description', f'{server_name} tools'),
                        'is_exclusive': server_config.get('is_exclusive', False),
                        'author': server_config.get('author', 'Unknown'),
                        'short_description': server_config.get('short_description', server_config.get('description', f'{server_name} tools')),
                        'help_email': server_config.get('help_email', '')
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
                        'help_email': server_config.get('help_email', '')
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
            backend_root = Path(__file__).parent.parent
            project_root = backend_root.parent
            help_paths = [
                project_root / "config" / "overrides" / help_config_filename,
                project_root / "config" / "defaults" / help_config_filename,
                backend_root / "configfilesadmin" / help_config_filename,
                backend_root / "configfiles" / help_config_filename,
                backend_root / help_config_filename,
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
    
# Log what the user can see for debugging
    logger.info(
        f"User {current_user} has access to {len(authorized_servers)} servers: {authorized_servers}\n"
        f"Returning {len(tools_info)} server tool groups to frontend for user {current_user}"
    )
    
    return {
        "app_name": app_settings.app_name,
        "models": list(llm_config.models.keys()),
        "tools": tools_info,  # Only authorized servers are included
        "prompts": prompts_info,  # Available prompts from authorized servers
        "data_sources": rag_data_sources,  # RAG data sources for the user
    "rag_servers": rag_servers,  # Optional richer structure for RAG UI
        "user": current_user,
    "is_in_admin_group": is_user_in_group(current_user, app_settings.admin_group),
        "active_sessions": 0,  # TODO: Implement session counting in ChatService
        "authorized_servers": authorized_servers,  # Optional: expose for debugging
            "rag_servers": rag_servers,  # Optional richer structure for RAG UI
        "agent_mode_available": app_settings.agent_mode_available,  # Whether agent mode UI should be shown
        "banner_enabled": app_settings.banner_enabled,  # Whether banner system is enabled
        "help_config": help_config,  # Help page configuration from help-config.json
        "features": {
            "workspaces": app_settings.feature_workspaces_enabled,
            "rag": app_settings.feature_rag_enabled,
            "tools": app_settings.feature_tools_enabled,
            "marketplace": app_settings.feature_marketplace_enabled,
            "files_panel": app_settings.feature_files_panel_enabled,
            "chat_history": app_settings.feature_chat_history_enabled
        }
    }


# @router.get("/sessions")
# async def get_session_info(current_user: str = Depends(get_current_user)):
#     """Get session information for the current user."""
#     # TODO: Implement session info retrieval from ChatService
#     return {
#         "total_sessions": 0,
#         "user_sessions": 0,
#         "sessions": []
#     }
