#!/usr/bin/env python3
"""
Debug script to extract JSON representation of all MCP servers and their functions.
This helps with debugging and understanding the MCP tool ecosystem.
"""

import asyncio
import json
import logging
import sys
import os
from pathlib import Path
from typing import Dict, Any, List

# Add backend to path so we can import modules
backend_path = Path(__file__).parent.parent / "backend"
sys.path.insert(0, str(backend_path))

from modules.mcp_tools.client import MCPToolManager
from modules.config.manager import config_manager

# Setup logging for debugging
logging.basicConfig(level=logging.WARNING)  # Reduce noise
logger = logging.getLogger(__name__)


def serialize_tool(tool) -> Dict[str, Any]:
    """Convert a tool object to a serializable dictionary."""
    try:
        return {
            "name": getattr(tool, 'name', None),
            "description": getattr(tool, 'description', None),
            "inputSchema": getattr(tool, 'inputSchema', None),
            "type": str(type(tool).__name__)
        }
    except Exception as e:
        return {
            "error": f"Failed to serialize tool: {str(e)}",
            "type": str(type(tool).__name__)
        }


def serialize_prompt(prompt) -> Dict[str, Any]:
    """Convert a prompt object to a serializable dictionary."""
    try:
        return {
            "name": getattr(prompt, 'name', None),
            "description": getattr(prompt, 'description', None),
            "arguments": getattr(prompt, 'arguments', None),
            "type": str(type(prompt).__name__)
        }
    except Exception as e:
        return {
            "error": f"Failed to serialize prompt: {str(e)}",
            "type": str(type(prompt).__name__)
        }


async def extract_mcp_debug_info() -> Dict[str, Any]:
    """Extract comprehensive debug information about MCP servers and tools."""
    
    print("🔍 Initializing MCP Tool Manager...")
    manager = MCPToolManager()
    
    debug_info = {
        "timestamp": asyncio.get_event_loop().time(),
        "config": {
            "servers_config": manager.servers_config,
            "config_path": manager.config_path
        },
        "servers": {},
        "summary": {
            "total_servers_configured": len(manager.servers_config),
            "total_servers_initialized": 0,
            "total_tools_discovered": 0,
            "total_prompts_discovered": 0,
            "failed_servers": [],
            "successful_servers": []
        }
    }
    
    try:
        print("🚀 Initializing MCP clients...")
        await manager.initialize_clients()
        
        debug_info["summary"]["total_servers_initialized"] = len(manager.clients)
        print(f"✅ Initialized {len(manager.clients)} clients")
        
        print("🔧 Discovering tools...")
        await manager.discover_tools()
        
        print("📝 Discovering prompts...")
        await manager.discover_prompts()
        
        # Process each server
        for server_name in manager.servers_config.keys():
            server_info = {
                "config": manager.servers_config.get(server_name, {}),
                "initialized": server_name in manager.clients,
                "tools": [],
                "prompts": [],
                "tool_count": 0,
                "prompt_count": 0,
                "errors": []
            }
            
            # Get tools
            if server_name in manager.available_tools:
                tools_data = manager.available_tools[server_name]
                tools_list = tools_data.get('tools', [])
                server_info["tool_count"] = len(tools_list)
                debug_info["summary"]["total_tools_discovered"] += len(tools_list)
                
                for tool in tools_list:
                    serialized_tool = serialize_tool(tool)
                    server_info["tools"].append(serialized_tool)
            
            # Get prompts
            if server_name in manager.available_prompts:
                prompts_data = manager.available_prompts[server_name]
                prompts_list = prompts_data.get('prompts', [])
                server_info["prompt_count"] = len(prompts_list)
                debug_info["summary"]["total_prompts_discovered"] += len(prompts_list)
                
                for prompt in prompts_list:
                    serialized_prompt = serialize_prompt(prompt)
                    server_info["prompts"].append(serialized_prompt)
            
            # Track success/failure
            if server_info["initialized"]:
                debug_info["summary"]["successful_servers"].append(server_name)
            else:
                debug_info["summary"]["failed_servers"].append(server_name)
                server_info["errors"].append("Failed to initialize client")
            
            debug_info["servers"][server_name] = server_info
            
            print(f"📊 {server_name}: {server_info['tool_count']} tools, {server_info['prompt_count']} prompts")
        
        # Add tool mapping information
        try:
            all_servers = list(manager.servers_config.keys())
            tools_info = manager.get_tools_for_servers(all_servers)
            debug_info["tool_schemas"] = tools_info.get("tools", [])
            debug_info["tool_mapping"] = tools_info.get("mapping", {})
        except Exception as e:
            debug_info["tool_schemas_error"] = str(e)
        
        # Add available tools list
        try:
            debug_info["available_tools_list"] = manager.get_available_tools()
        except Exception as e:
            debug_info["available_tools_error"] = str(e)
        
    except Exception as e:
        print(f"❌ Error during discovery: {e}")
        debug_info["discovery_error"] = str(e)
    finally:
        try:
            await manager.cleanup()
        except Exception as e:
            debug_info["cleanup_error"] = str(e)
    
    return debug_info


async def main():
    """Main function to run the debug extraction."""
    print("🚀 Starting MCP Server Debug Extraction")
    print("=" * 50)
    
    try:
        debug_info = await extract_mcp_debug_info()
        
        # Save to JSON file
        output_file = Path(__file__).parent / "mcp_debug_info.json"
        
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(debug_info, f, indent=2, ensure_ascii=False, default=str)
        
        print("\n" + "=" * 50)
        print("📊 SUMMARY")
        print("=" * 50)
        summary = debug_info["summary"]
        print(f"🔧 Configured servers: {summary['total_servers_configured']}")
        print(f"✅ Successfully initialized: {summary['total_servers_initialized']}")
        print(f"🛠️  Total tools discovered: {summary['total_tools_discovered']}")
        print(f"📝 Total prompts discovered: {summary['total_prompts_discovered']}")
        
        if summary["successful_servers"]:
            print(f"✅ Successful servers: {', '.join(summary['successful_servers'])}")
        
        if summary["failed_servers"]:
            print(f"❌ Failed servers: {', '.join(summary['failed_servers'])}")
        
        print(f"\n💾 Debug info saved to: {output_file.absolute()}")
        print(f"📁 File size: {output_file.stat().st_size} bytes")
        
        return 0
        
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        return 1


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)