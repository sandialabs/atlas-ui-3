"""Admin routes for configuration management and system monitoring.

Provides admin-only endpoints for: banners, configuration files, logs, and (commented) health checks.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.auth import is_user_in_group
from core.log_sanitizer import get_current_user, sanitize_for_logging
from modules.config import config_manager
from infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin"])


class AdminConfigUpdate(BaseModel):
    content: str
    file_type: str  # 'json', 'yaml', 'text'


class BannerMessageUpdate(BaseModel):
    messages: List[str]


class MCPServerAction(BaseModel):
    server_name: str


async def require_admin(current_user: str = Depends(get_current_user)) -> str:
    admin_group = config_manager.app_settings.admin_group
    if not await is_user_in_group(current_user, admin_group):
        raise HTTPException(
            status_code=403,
            detail=f"Admin access required. User must be in '{admin_group}' group.",
        )
    return current_user


def _resolve_config_write_root() -> Path:
    """Resolve the config directory used for admin edits.

    If APP_CONFIG_OVERRIDES is explicitly set, we create/use that path.
    Otherwise, we use overrides if it already exists; if not, fall back to defaults.
    """
    app_settings = config_manager.app_settings
    overrides_root = Path(app_settings.app_config_overrides)
    defaults_root = Path(app_settings.app_config_defaults)

    # If relative paths, resolve from project root
    project_root = Path(__file__).parent.parent.parent
    if not overrides_root.is_absolute():
        overrides_root = project_root / overrides_root
    if not defaults_root.is_absolute():
        defaults_root = project_root / defaults_root

    if "APP_CONFIG_OVERRIDES" in os.environ:
        overrides_root.mkdir(parents=True, exist_ok=True)
        return overrides_root

    if overrides_root.exists():
        return overrides_root

    defaults_root.mkdir(parents=True, exist_ok=True)
    return defaults_root


def setup_config_overrides() -> None:
    """Legacy hook retained for tests; resolves config write root."""
    _resolve_config_write_root()


def get_admin_config_path(filename: str) -> Path:
    # Get config filename mappings from config manager
    app_settings = config_manager.app_settings
    
    # Map standard filenames to potentially overridden ones
    if filename == "messages.txt":
        custom_filename = app_settings.messages_config_file
    elif filename == "help-config.json":
        custom_filename = app_settings.help_config_file
    elif filename == "mcp.json":
        custom_filename = app_settings.mcp_config_file
    elif filename == "llmconfig.yml":
        custom_filename = app_settings.llm_config_file
    else:
        custom_filename = filename
    
    base = _resolve_config_write_root()
    return base / custom_filename


def get_file_content(file_path: Path) -> str:
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File {file_path.name} not found")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error reading file {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")


def write_file_content(file_path: Path, content: str, file_type: str = "text") -> None:
    try:
        if file_type == "json":
            json.loads(content)
        elif file_type == "yaml":
            yaml.safe_load(content)

        temp_path = file_path.with_suffix(file_path.suffix + ".tmp")
        if temp_path.exists():
            temp_path.unlink()
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(content)
        if os.name == "nt" and file_path.exists():  # Windows atomic rename safety
            file_path.unlink()
        temp_path.rename(file_path)
        logger.info(f"Updated config file {file_path}")
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid {file_type.upper()}: {e}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error writing file {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Error writing file: {e}")


def _project_root() -> Path:
    # routes/admin_routes.py -> backend/routes -> project root is 2 levels up
    return Path(__file__).resolve().parents[2]


def _log_base_dir() -> Path:
    app_settings = config_manager.app_settings
    if app_settings.app_log_dir:
        return Path(app_settings.app_log_dir)
    return _project_root() / "logs"


def _locate_log_file() -> Path:
    """Locate the log file (standardized on project_root/logs with optional override).

    Priority:
    1. APP_LOG_DIR (env) if set
    2. ./logs
    3. Legacy fallbacks (backend/logs, runtime/logs) for backward compatibility
    """
    base = _log_base_dir()
    candidates = [
        base / "app.jsonl",
        base / "app.log",
        Path("logs/app.jsonl"),
        Path("logs/app.log"),
        Path("backend/logs/app.jsonl"),  # legacy
        Path("backend/logs/app.log"),     # legacy
        Path("runtime/logs/app.jsonl"),   # legacy
        Path("runtime/logs/app.log"),     # legacy
    ]
    for c in candidates:
        if c.exists():
            return c
    raise HTTPException(status_code=404, detail="Log file not found")


@admin_router.get("/")
async def admin_dashboard(admin_user: str = Depends(require_admin)):
    return {
        "message": "Admin Dashboard",
        "user": admin_user,
        "available_endpoints": [
            "/admin/banners",
            "/admin/logs/viewer",
            "/admin/logs/clear",
            "/admin/logs/download",
            "/admin/mcp/reload",
            "/admin/mcp/reconnect",
            "/admin/mcp/status",
        ],
    }


@admin_router.get("/banners")
async def get_banner_config(admin_user: str = Depends(require_admin)):
    try:
        setup_config_overrides()
        messages_file = get_admin_config_path("messages.txt")
        if not messages_file.exists():
            write_file_content(messages_file, "System status: All services operational\n")
        content = get_file_content(messages_file)
        messages = [ln.strip() for ln in content.splitlines() if ln.strip()]
        return {
            "messages": messages,
            "file_path": str(messages_file),
            "last_modified": messages_file.stat().st_mtime,
            "banner_enabled": config_manager.app_settings.banner_enabled,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting banner config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/banners")
async def update_banner_config(
    update: BannerMessageUpdate, admin_user: str = Depends(require_admin)
):
    messages_file = None
    try:
        setup_config_overrides()
        messages_file = get_admin_config_path("messages.txt")
        content = ("\n".join(update.messages) + "\n") if update.messages else ""
        write_file_content(messages_file, content)
        logger.info(
            f"Banner messages successfully saved to disk at {sanitize_for_logging(str(messages_file))} "
            f"by {sanitize_for_logging(admin_user)}"
        )
        return {
            "message": "Banner messages updated successfully",
            "messages": update.messages,
            "updated_by": admin_user,
        }
    except Exception as e:  # noqa: BLE001
        file_path_str = sanitize_for_logging(str(messages_file)) if messages_file else "unknown path"
        logger.error(
            f"Failed to save banner messages to disk at {file_path_str}: {e}"
        )
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/mcp/reload")
async def reload_mcp_servers(admin_user: str = Depends(require_admin)):
    """Reload MCP servers from disk configuration and reinitialize connections.
    
    This endpoint:
    1. Reloads the mcp.json configuration from disk (hot-reload)
    2. Reinitializes all MCP client connections
    3. Rediscovers tools and prompts from all servers
    
    Use this after modifying the mcp.json configuration file to apply changes
    without restarting the application.
    """
    try:
        mcp = app_factory.get_mcp_manager()
        
        # Reload config from disk first
        config_changes = mcp.reload_config()
        
        # Re-initialize clients and rediscover
        await mcp.initialize_clients()
        await mcp.discover_tools()
        await mcp.discover_prompts()
        
        return {
            "message": "MCP servers reloaded from disk configuration",
            "config_changes": config_changes,
            "servers": list(mcp.clients.keys()),
            "failed_servers": list(mcp.get_failed_servers().keys()),
            "tool_counts": {k: len(v.get("tools", [])) for k, v in mcp.available_tools.items()},
            "prompt_counts": {k: len(v.get("prompts", [])) for k, v in mcp.available_prompts.items()},
            "reloaded_by": admin_user,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error reloading MCP servers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/mcp/reconnect")
async def reconnect_failed_mcp_servers(admin_user: str = Depends(require_admin)):
    """Attempt to reconnect to MCP servers that previously failed.
    
    This endpoint manually triggers reconnection attempts for servers that failed
    to connect during initialization or previous reconnection attempts.
    Respects exponential backoff unless force=true is specified.
    """
    try:
        mcp = app_factory.get_mcp_manager()
        # Admin-triggered reconnect should bypass backoff and try immediately
        result = await mcp.reconnect_failed_servers(force=True)
        
        return {
            "message": "Reconnection attempt completed",
            "result": result,
            "current_servers": list(mcp.clients.keys()),
            "failed_servers": mcp.get_failed_servers(),
            "triggered_by": admin_user,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error reconnecting MCP servers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.get("/mcp/status")
async def get_mcp_status(admin_user: str = Depends(require_admin)):
    """Get current MCP server connection status.
    
    Returns information about:
    - Currently connected servers
    - Failed servers with error details and backoff info
    - Auto-reconnect feature status
    """
    try:
        mcp = app_factory.get_mcp_manager()
        app_settings = config_manager.app_settings
        
        failed_servers = mcp.get_failed_servers()
        
        # Calculate next retry time for each failed server
        current_time = time.time()
        failed_servers_with_timing = {}
        for server_name, failure_info in failed_servers.items():
            backoff_delay = mcp._calculate_backoff_delay(failure_info["attempt_count"])
            time_since_last = current_time - failure_info["last_attempt"]
            next_retry_in = max(0, backoff_delay - time_since_last)
            
            failed_servers_with_timing[server_name] = {
                **failure_info,
                "backoff_delay": backoff_delay,
                "next_retry_in_seconds": next_retry_in,
            }
        
        # A server is considered "connected" only if it has a client AND
        # at least one tool or prompt discovered (or explicitly marked as
        # having zero tools/prompts but no recorded failure). This prevents
        # HTTP/SSE/SSL discovery failures from showing as connected.
        connected_servers: List[str] = []
        for server_name in mcp.clients.keys():
            tools = mcp.available_tools.get(server_name, {}).get("tools", [])
            prompts = mcp.available_prompts.get(server_name, {}).get("prompts", [])
            if tools or prompts:
                connected_servers.append(server_name)
            elif server_name not in failed_servers_with_timing:
                # No tools/prompts but also no recorded failure; treat as connected
                # to preserve behavior for servers that legitimately expose nothing.
                connected_servers.append(server_name)

        return {
            "connected_servers": connected_servers,
            "configured_servers": list(mcp.servers_config.keys()),
            "failed_servers": failed_servers_with_timing,
            "auto_reconnect": {
                "enabled": app_settings.feature_mcp_auto_reconnect_enabled,
                "base_interval": app_settings.mcp_reconnect_interval,
                "max_interval": app_settings.mcp_reconnect_max_interval,
                "backoff_multiplier": app_settings.mcp_reconnect_backoff_multiplier,
                "running": mcp._reconnect_running,
            },
            "tool_counts": {k: len(v.get("tools", [])) for k, v in mcp.available_tools.items()},
            "prompt_counts": {k: len(v.get("prompts", [])) for k, v in mcp.available_prompts.items()},
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting MCP status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



# --- Config Viewer ---
@admin_router.get("/config/view")
async def get_all_configs(admin_user: str = Depends(require_admin)):
    """Get all configuration values for admin viewing."""
    try:
        # Get all configs from config manager
        app_settings = config_manager.app_settings
        llm_config = config_manager.llm_config
        mcp_config = config_manager.mcp_config
        
        # Convert app_settings to dict, excluding sensitive fields
        app_settings_dict = app_settings.model_dump()
        
        # Mask sensitive fields
        sensitive_fields = ['api_key', 'secret', 'password', 'token']
        for key, value in app_settings_dict.items():
            if any(sensitive in key.lower() for sensitive in sensitive_fields):
                if isinstance(value, str) and value:
                    app_settings_dict[key] = "***MASKED***"
        
        # Convert LLM config, masking API keys
        llm_config_dict = llm_config.model_dump()
        if 'models' in llm_config_dict:
            for model_name, model_config in llm_config_dict['models'].items():
                if 'api_key' in model_config and model_config['api_key']:
                    model_config['api_key'] = "***MASKED***"
        
        # Convert MCP config
        mcp_config_dict = mcp_config.model_dump()
        
        return {
            "app_settings": app_settings_dict,
            "llm_config": llm_config_dict, 
            "mcp_config": mcp_config_dict,
            "config_validation": config_manager.validate_config()
        }
    except Exception as e:
        logger.error(f"Error getting config view: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- Log Management ---

@admin_router.get("/logs/viewer")
async def get_enhanced_logs(
    lines: int = 500,
    level_filter: Optional[str] = None,
    module_filter: Optional[str] = None,
    admin_user: str = Depends(require_admin),  # noqa: ARG001 (enforces auth)
):
    try:
        base_dir = _log_base_dir()
        log_file = base_dir / "app.jsonl"
        if not log_file.exists():
            print(f"Log file {log_file.absolute()} not found")
            raise HTTPException(status_code=404, detail="Log file not found")

        from collections import deque
        entries: List[Dict[str, Any]] = []
        modules: set[str] = set()
        levels: set[str] = set()

        try:
            with log_file.open("r", encoding="utf-8") as f:
                recent_lines = deque(f, maxlen=lines + 200)
            import re
            pattern = re.compile(
                r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,\s-]*(\w+)[,\s-]*([^-]*)[,\s-]*(.*)"
            )
            for raw in recent_lines:
                raw = raw.strip()
                if not raw or raw == "NEW LOG":
                    continue
                try:
                    entry = json.loads(raw)
                    processed = {
                        "timestamp": entry.get("timestamp", ""),
                        "level": entry.get("level", "UNKNOWN"),
                        "module": entry.get("module", entry.get("logger", "")),
                        "logger": entry.get("logger", ""),
                        "function": entry.get("function", ""),
                        "message": entry.get("message", ""),
                        "trace_id": entry.get("trace_id", ""),
                        "span_id": entry.get("span_id", ""),
                        "line": entry.get("line", ""),
                        "thread_name": entry.get("thread_name", ""),
                        "extras": {k: v for k, v in entry.items() if k.startswith("extra_")},
                    }
                except json.JSONDecodeError:
                    m = pattern.match(raw)
                    if m:
                        ts, lvl, mod, msg = m.groups()
                        processed = {
                            "timestamp": ts.strip(),
                            "level": lvl.strip().upper(),
                            "module": mod.strip(),
                            "logger": mod.strip(),
                            "function": "",
                            "message": msg.strip(),
                            "trace_id": "",
                            "span_id": "",
                            "line": "",
                            "thread_name": "",
                            "extras": {},
                        }
                    else:
                        processed = {
                            "timestamp": "",
                            "level": "INFO",
                            "module": "unknown",
                            "logger": "unknown",
                            "function": "",
                            "message": raw,
                            "trace_id": "",
                            "span_id": "",
                            "line": "",
                            "thread_name": "",
                            "extras": {},
                        }
                if level_filter and processed["level"] != level_filter:
                    continue
                if module_filter and processed["module"] != module_filter:
                    continue
                entries.append(processed)
                modules.add(processed["module"])
                levels.add(processed["level"])
                if len(entries) >= lines:
                    break
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error reading log file {log_file}: {e}")
            entries = [
                {
                    "timestamp": "",
                    "level": "ERROR",
                    "module": "admin",
                    "logger": "admin",
                    "function": "get_enhanced_logs",
                    "message": "An internal error occurred while reading log file.",
                    "trace_id": "",
                    "span_id": "",
                    "line": "",
                    "thread_name": "",
                    "extras": {},
                }
            ]
            modules = {"admin"}
            levels = {"ERROR"}

        return {
            "entries": entries,
            "metadata": {
                "total_entries": len(entries),
                "unique_modules": sorted(modules),
                "unique_levels": sorted(levels),
                "log_file_path": str(log_file),
                "requested_lines": lines,
                "filters_applied": {"level": level_filter, "module": module_filter},
            },
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting enhanced logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/logs/clear")
async def clear_app_logs(admin_user: str = Depends(require_admin)):
    try:
        base = _log_base_dir()
        candidates = [
            base / "app.jsonl",
            base / "app.log",
            Path("logs/app.jsonl"),        # explicit root fallback
            Path("logs/app.log"),          # explicit root fallback
            Path("backend/logs/app.jsonl"),  # legacy
            Path("backend/logs/app.log"),     # legacy
            Path("runtime/logs/app.jsonl"),   # legacy
            Path("runtime/logs/app.log"),     # legacy,
        ]
        cleared: List[str] = []
        for f in candidates:
            if f.exists():
                try:
                    f.write_text("NEW LOG\n", encoding="utf-8")
                    cleared.append(str(f))
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Failed clearing {f}: {e}")
        if not cleared:
            return {"message": "No log files found to clear", "cleared_by": admin_user, "files_cleared": []}
        sanitized_admin_user = sanitize_for_logging(admin_user)
        logger.info(f"Log files cleared by {sanitized_admin_user}: {cleared}")
        return {"message": "Log files cleared successfully", "cleared_by": admin_user, "files_cleared": cleared}
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error clearing logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.get("/logs/download")
async def download_logs(admin_user: str = Depends(require_admin)):
    """Download the raw application log file.

    Frontend sets a custom filename via the anchor `download` attribute, so we just
    stream the file with a generic name. Uses same discovery logic as log viewer.
    """
    try:
        log_file = _locate_log_file()
        # Choose media type: jsonl logs are still plain text; no compression here.
        media_type = "application/json" if log_file.suffix == ".jsonl" else "text/plain"
        return FileResponse(
            path=str(log_file),
            media_type=media_type,
            filename=log_file.name,
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error preparing log download: {e}")
        raise HTTPException(status_code=500, detail="Error preparing log download")


# --- System Status (minimal) ---

@admin_router.get("/system-status")
async def get_system_status(admin_user: str = Depends(require_admin)):
    """Minimal system status endpoint for the Admin UI.

    Returns basic configuration and logging status; avoids heavy checks.
    """
    try:
        # Configuration status: overrides directory and file count
        app_settings = config_manager.app_settings
        overrides_root = Path(app_settings.app_config_overrides)
        if not overrides_root.is_absolute():
            project_root = _project_root()
            overrides_root = project_root / overrides_root
        overrides_root.mkdir(parents=True, exist_ok=True)
        config_files = list(overrides_root.glob("*"))
        config_status = "healthy" if config_files else "warning"

        # Logging status
        log_dir = _log_base_dir()
        log_file = log_dir / "app.jsonl"
        log_exists = log_file.exists()
        logging_status = "healthy" if log_exists else "warning"

        components = [
            {
                "component": "Configuration",
                "status": config_status,
                "details": {
                    "overrides_dir": str(overrides_root),
                    "files_count": len(config_files),
                },
            },
            {
                "component": "Logging",
                "status": logging_status,
                "details": {
                    "log_file": str(log_file),
                    "exists": log_exists,
                    "size_bytes": log_file.stat().st_size if log_exists else 0,
                },
            },
        ]

        overall = "healthy" if all(c["status"] == "healthy" for c in components) else "warning"
        return {
            "overall_status": overall,
            "components": components,
            "checked_by": admin_user,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting system status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# --- MCP Server Management ---

@admin_router.get("/mcp/available-servers")
async def get_available_mcp_servers(
    admin_user: str = Depends(require_admin),  # noqa: ARG001 (enforces auth)
):
    """Get all available MCP servers from the example-configs directory."""
    try:
        project_root = _project_root()
        example_configs_dir = project_root / "config" / "mcp-example-configs"
        
        if not example_configs_dir.exists():
            return {"available_servers": {}}
        
        available_servers = {}
        
        for config_file in example_configs_dir.glob("mcp-*.json"):
            try:
                with config_file.open("r", encoding="utf-8") as f:
                    config_data = json.load(f)
                
                # Each file should contain one server config
                for server_name, server_config in config_data.items():
                    available_servers[server_name] = {
                        "config": server_config,
                        "source_file": config_file.name,
                        "description": server_config.get("description", ""),
                        "short_description": server_config.get("short_description", ""),
                        "author": server_config.get("author", ""),
                        "compliance_level": server_config.get("compliance_level", "")
                    }
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"Failed to parse {config_file.name}: {e}")
                continue
        
        return {"available_servers": available_servers}
    
    except Exception as e:
        logger.error(f"Error getting available MCP servers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.get("/mcp/active-servers")
async def get_active_mcp_servers(
    admin_user: str = Depends(require_admin),  # noqa: ARG001 (enforces auth)
):
    """Get currently active MCP servers from the overrides/mcp.json file."""
    try:
        mcp_config_path = get_admin_config_path("mcp.json")
        
        if not mcp_config_path.exists():
            return {"active_servers": {}}
        
        with mcp_config_path.open("r", encoding="utf-8") as f:
            active_config = json.load(f)
        
        return {"active_servers": active_config}
    
    except Exception as e:
        logger.error(f"Error getting active MCP servers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/mcp/add-server")
async def add_mcp_server(
    action: MCPServerAction,
    admin_user: str = Depends(require_admin),  # noqa: ARG001 (enforces auth)
):
    """Add an MCP server from example-configs to the active configuration."""
    try:
        server_name = action.server_name
        
        # Get the server config from example-configs
        project_root = _project_root()
        example_configs_dir = project_root / "config" / "mcp-example-configs"
        
        server_config = None
        for config_file in example_configs_dir.glob("mcp-*.json"):
            try:
                with config_file.open("r", encoding="utf-8") as f:
                    config_data = json.load(f)
                
                if server_name in config_data:
                    server_config = config_data[server_name]
                    break
            except (json.JSONDecodeError, Exception):
                continue
        
        if not server_config:
            raise HTTPException(
                status_code=404, 
                detail=f"Server '{server_name}' not found in example configurations"
            )
        
        # Load current active configuration
        mcp_config_path = get_admin_config_path("mcp.json")
        
        if mcp_config_path.exists():
            with mcp_config_path.open("r", encoding="utf-8") as f:
                active_config = json.load(f)
        else:
            active_config = {}
        
        # Check if server is already active
        if server_name in active_config:
            return {
                "message": f"Server '{server_name}' is already active",
                "server_name": server_name,
                "already_active": True
            }
        
        # Add the server to active configuration
        active_config[server_name] = server_config
        
        # Save the updated configuration
        with mcp_config_path.open("w", encoding="utf-8") as f:
            json.dump(active_config, f, indent=2)

        sanitized_admin_user = sanitize_for_logging(admin_user)
        sanitized_server_name = sanitize_for_logging(server_name)
        logger.info(f"Admin {sanitized_admin_user} added MCP server '{sanitized_server_name}' to active configuration")

        # Trigger MCP reload to apply changes
        try:
            mcp_manager = app_factory.get_mcp_manager()
            if mcp_manager:
                await mcp_manager.reload_servers()
        except Exception as reload_error:
            sanitized_server_name = sanitize_for_logging(server_name)
            logger.warning(f"Failed to reload MCP servers after adding '{sanitized_server_name}': {reload_error}")
        
        return {
            "message": f"Server '{server_name}' added successfully",
            "server_name": server_name,
            "config": server_config
        }
    
    except HTTPException:
        raise
    except Exception as e:
        sanitized_server_name = sanitize_for_logging(action.server_name)
        logger.error(f"Error adding MCP server '{sanitized_server_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/mcp/remove-server")
async def remove_mcp_server(
    action: MCPServerAction,
    admin_user: str = Depends(require_admin),  # noqa: ARG001 (enforces auth)
):
    """Remove an MCP server from the active configuration."""
    try:
        server_name = action.server_name
        
        # Load current active configuration
        mcp_config_path = get_admin_config_path("mcp.json")
        
        if not mcp_config_path.exists():
            raise HTTPException(
                status_code=404, 
                detail="MCP configuration file not found"
            )
        
        with mcp_config_path.open("r", encoding="utf-8") as f:
            active_config = json.load(f)
        
        # Check if server exists in active configuration
        if server_name not in active_config:
            return {
                "message": f"Server '{server_name}' is not currently active",
                "server_name": server_name,
                "not_active": True
            }
        
        # Remove the server from active configuration
        removed_config = active_config.pop(server_name)
        
        # Save the updated configuration
        with mcp_config_path.open("w", encoding="utf-8") as f:
            json.dump(active_config, f, indent=2)

        sanitized_admin_user = sanitize_for_logging(admin_user)
        sanitized_server_name = sanitize_for_logging(server_name)
        logger.info(f"Admin {sanitized_admin_user} removed MCP server '{sanitized_server_name}' from active configuration")

        # Trigger MCP reload to apply changes
        try:
            mcp_manager = app_factory.get_mcp_manager()
            if mcp_manager:
                await mcp_manager.reload_servers()
        except Exception as reload_error:
            sanitized_server_name = sanitize_for_logging(server_name)
            logger.warning(f"Failed to reload MCP servers after removing '{sanitized_server_name}': {reload_error}")
        
        return {
            "message": f"Server '{server_name}' removed successfully",
            "server_name": server_name,
            "removed_config": removed_config
        }
    
    except HTTPException:
        raise
    except Exception as e:
        sanitized_server_name = sanitize_for_logging(action.server_name)
        logger.error(f"Error removing MCP server '{sanitized_server_name}': {e}")
        raise HTTPException(status_code=500, detail=str(e))
