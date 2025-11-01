"""Tool authorization policy - filters tools based on user access control."""

import logging
from typing import List, Optional, Callable, Any

from core.auth_utils import create_authorization_manager

logger = logging.getLogger(__name__)


class ToolAuthorizationService:
    """
    Service that filters selected tools based on user authorization.
    
    Enforces MCP tool access control lists (ACLs) by checking:
    - User authorization to MCP servers
    - Special cases (e.g., canvas_canvas tool is always allowed)
    """

    def __init__(self, tool_manager: Optional[Any] = None):
        """
        Initialize the tool authorization service.
        
        Args:
            tool_manager: Optional tool manager with server configuration
        """
        self.tool_manager = tool_manager

    async def filter_authorized_tools(
        self,
        selected_tools: List[str],
        user_email: Optional[str] = None,
    ) -> List[str]:
        """
        Filter tools to only those the user is authorized to use.
        
        Args:
            selected_tools: List of tool names (format: "server_toolname")
            user_email: Email of the user making the request
            
        Returns:
            Filtered list of authorized tool names
        """
        if not selected_tools or not self.tool_manager:
            return selected_tools or []

        try:
            user = user_email or ""
            
            # Get authorized servers for this user
            authorized_servers = self._get_authorized_servers(user)
            
            # Filter tools by server prefix
            filtered_tools: List[str] = []
            for tool in selected_tools:
                # Special case: canvas_canvas is always allowed
                if tool == "canvas_canvas":
                    filtered_tools.append(tool)
                    continue
                
                # Check if tool belongs to an authorized server
                if isinstance(tool, str) and "_" in tool:
                    # Match against authorized servers by checking if tool name starts with server_
                    # This handles server names that contain underscores (e.g., "pptx_generator")
                    matched_server = None
                    for auth_server in authorized_servers:
                        if tool.startswith(f"{auth_server}_"):
                            matched_server = auth_server
                            break
                    
                    if matched_server:
                        filtered_tools.append(tool)
            
            return filtered_tools
            
        except Exception:
            logger.debug(
                "Tool ACL filtering failed; proceeding with original selection",
                exc_info=True
            )
            return selected_tools

    def _get_authorized_servers(self, user: str) -> List[str]:
        """
        Get list of MCP servers the user is authorized to access.
        
        Args:
            user: User email
            
        Returns:
            List of authorized server names
        """
        # Prefer tool_manager's own authorization method if available
        if hasattr(self.tool_manager, "get_authorized_servers"):
            return self.tool_manager.get_authorized_servers(user, None)  # type: ignore[attr-defined]
        
        # Fallback to authorization manager
        auth_mgr = create_authorization_manager()
        servers_config = getattr(self.tool_manager, "servers_config", {})
        return auth_mgr.filter_authorized_servers(
            user,
            servers_config,
            getattr(self.tool_manager, "get_server_groups", lambda s: []),
        )
