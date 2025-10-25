"""Tools interface protocols."""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from domain.messages.models import ToolCall, ToolResult


@runtime_checkable
class ToolProtocol(Protocol):
    """Protocol for individual tools."""
    
    @property
    def name(self) -> str:
        """Tool name."""
        ...
    
    @property
    def description(self) -> str:
        """Tool description."""
        ...
    
    def get_schema(self) -> Dict[str, Any]:
        """Get tool schema for LLM."""
        ...
    
    async def execute(self, arguments: Dict[str, Any]) -> ToolResult:
        """Execute the tool with given arguments."""
        ...


@runtime_checkable
class ToolManagerProtocol(Protocol):
    """Protocol for tool management."""
    
    def get_available_tools(self) -> List[str]:
        """Get list of available tool names."""
        ...
    
    def get_tools_schema(self, tool_names: List[str]) -> List[Dict[str, Any]]:
        """Get schemas for specified tools."""
        ...
    
    async def execute_tool(
        self,
        tool_call: ToolCall,
        context: Optional[Dict[str, Any]] = None
    ) -> ToolResult:
        """Execute a tool call."""
        ...
    
    async def execute_tool_calls(
        self,
        tool_calls: List[ToolCall],
        context: Optional[Dict[str, Any]] = None
    ) -> List[ToolResult]:
        """Execute multiple tool calls."""
        ...
