"""Admin routes for configuration management and system monitoring.

Provides admin-only endpoints for: banners, configuration files, logs, and (commented) health checks.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.auth import is_user_in_group
from core.utils import get_current_user
from modules.config import config_manager
from core.otel_config import get_otel_config  # noqa: F401 (may be used later)
from infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin", tags=["admin"])


class AdminConfigUpdate(BaseModel):
    content: str
    file_type: str  # 'json', 'yaml', 'text'


class BannerMessageUpdate(BaseModel):
    messages: List[str]


class SystemStatus(BaseModel):  # noqa: F841 (kept for future use)
    component: str
    status: str
    details: Optional[Dict[str, Any]] = None


def require_admin(current_user: str = Depends(get_current_user)) -> str:
    admin_group = config_manager.app_settings.admin_group
    if not is_user_in_group(current_user, admin_group):
        raise HTTPException(
            status_code=403,
            detail=f"Admin access required. User must be in '{admin_group}' group.",
        )
    return current_user


def setup_config_overrides() -> None:
    """Ensure editable overrides directory exists; seed from defaults / legacy if empty."""
    overrides_root = Path(os.getenv("APP_CONFIG_OVERRIDES", "config/overrides"))
    defaults_root = Path(os.getenv("APP_CONFIG_DEFAULTS", "config/defaults"))
    overrides_root.mkdir(parents=True, exist_ok=True)
    defaults_root.mkdir(parents=True, exist_ok=True)

    if any(overrides_root.iterdir()):
        return

    logger.info("Seeding empty overrides directory")
    seed_sources = [
        defaults_root,
        Path("backend/configfilesadmin"),
        Path("backend/configfiles"),
        Path("configfilesadmin"),
        Path("configfiles"),
    ]
    for source in seed_sources:
        if source.exists() and any(source.iterdir()):
            for file_path in source.glob("*"):
                if file_path.is_file():
                    dest = overrides_root / file_path.name
                    try:
                        shutil.copy2(file_path, dest)
                        logger.info(f"Copied seed config {file_path} -> {dest}")
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"Failed seeding {file_path}: {e}")
            break


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
    
    # Use same logic as config manager to resolve relative paths from project root
    overrides_env = os.getenv("APP_CONFIG_OVERRIDES", "config/overrides")
    base = Path(overrides_env)
    
    # If relative path, resolve from project root (parent of backend directory)
    if not base.is_absolute():
        project_root = Path(__file__).parent.parent.parent  # Go up from routes/ to backend/ to project root
        base = project_root / base
    
    base.mkdir(parents=True, exist_ok=True)
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
    env_path = os.getenv("APP_LOG_DIR")
    if env_path:
        return Path(env_path)
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
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting banner config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/banners")
async def update_banner_config(
    update: BannerMessageUpdate, admin_user: str = Depends(require_admin)
):
    try:
        setup_config_overrides()
        messages_file = get_admin_config_path("messages.txt")
        content = ("\n".join(update.messages) + "\n") if update.messages else ""
        write_file_content(messages_file, content)
        logger.info(f"Banner messages updated by {admin_user}")
        return {
            "message": "Banner messages updated successfully",
            "messages": update.messages,
            "updated_by": admin_user,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error updating banner config: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@admin_router.post("/mcp/reload")
async def reload_mcp_servers(admin_user: str = Depends(require_admin)):
    """Reload MCP servers (clients, tools, prompts)."""
    try:
        mcp = app_factory.get_mcp_manager()
        # Re-initialize clients and rediscover
        await mcp.initialize_clients()
        await mcp.discover_tools()
        await mcp.discover_prompts()
        return {
            "message": "MCP servers reloaded",
            "servers": list(mcp.clients.keys()),
            "tool_counts": {k: len(v.get("tools", [])) for k, v in mcp.available_tools.items()},
            "prompt_counts": {k: len(v.get("prompts", [])) for k, v in mcp.available_prompts.items()},
            "reloaded_by": admin_user,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error reloading MCP servers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# # --- MCP Configuration ---

# @admin_router.get("/mcp-config")
# async def get_mcp_config(admin_user: str = Depends(require_admin)):
#     """Get current MCP server configuration."""
#     try:
#         mcp_file = get_admin_config_path("mcp.json")
#         content = get_file_content(mcp_file)
        
#         return {
#             "content": content,
#             "parsed": json.loads(content),
#             "file_path": str(mcp_file),
#             "last_modified": mcp_file.stat().st_mtime
#         }
#     except Exception as e:
#         logger.error(f"Error getting MCP config: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


# @admin_router.post("/mcp-config")
# async def update_mcp_config(
#     update: AdminConfigUpdate,
#     admin_user: str = Depends(require_admin)
# ):
#     """Update MCP server configuration."""
#     try:
#         mcp_file = get_admin_config_path("mcp.json")
#         write_file_content(mcp_file, update.content, "json")
        
#         logger.info(f"MCP configuration updated by {admin_user}")
#         return {
#             "message": "MCP configuration updated successfully",
#             "updated_by": admin_user
#         }
#     except Exception as e:
#         logger.error(f"Error updating MCP config: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


# # --- LLM Configuration ---

# @admin_router.get("/llm-config")
# async def get_llm_config(admin_user: str = Depends(require_admin)):
#     """Get current LLM configuration."""
#     try:
#         llm_file = get_admin_config_path("llmconfig.yml")
#         content = get_file_content(llm_file)
        
#         return {
#             "content": content,
#             "parsed": yaml.safe_load(content),
#             "file_path": str(llm_file),
#             "last_modified": llm_file.stat().st_mtime
#         }
#     except Exception as e:
#         logger.error(f"Error getting LLM config: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


# @admin_router.post("/llm-config")
# async def update_llm_config(
#     update: AdminConfigUpdate,
#     admin_user: str = Depends(require_admin)
# ):
#     """Update LLM configuration."""
#     try:
#         llm_file = get_admin_config_path("llmconfig.yml")
#         write_file_content(llm_file, update.content, "yaml")
        
#         logger.info(f"LLM configuration updated by {admin_user}")
#         return {
#             "message": "LLM configuration updated successfully", 
#             "updated_by": admin_user
#         }
#     except Exception as e:
#         logger.error(f"Error updating LLM config: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


# # --- Help Configuration ---

# @admin_router.get("/help-config")
# async def get_help_config(admin_user: str = Depends(require_admin)):
#     """Get current help configuration."""
#     try:
#         help_file = get_admin_config_path("help-config.json")
#         content = get_file_content(help_file)
        
#         return {
#             "content": content,
#             "parsed": json.loads(content),
#             "file_path": str(help_file),
#             "last_modified": help_file.stat().st_mtime
#         }
#     except Exception as e:
#         logger.error(f"Error getting help config: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


# @admin_router.post("/help-config")
# async def update_help_config(
#     update: AdminConfigUpdate,
#     admin_user: str = Depends(require_admin)
# ):
#     """Update help configuration."""
#     try:
#         help_file = get_admin_config_path("help-config.json")
#         write_file_content(help_file, update.content, "json")
        
#         logger.info(f"Help configuration updated by {admin_user}")
#         return {
#             "message": "Help configuration updated successfully",
#             "updated_by": admin_user
#         }
#     except Exception as e:
#         logger.error(f"Error updating help config: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


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
                    "message": f"Error reading log file: {e}",
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
        logger.info(f"Log files cleared by {admin_user}: {cleared}")
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


# # --- System Status ---

# @admin_router.get("/system-status")
# async def get_system_status(admin_user: str = Depends(require_admin)):
#     """Get overall system status including MCP servers and LLM health."""
#     try:
#         status_info = []
        
#         # Check if configfilesadmin exists and has files
#         admin_config_dir = Path("configfilesadmin")
#         config_status = "healthy" if admin_config_dir.exists() and any(admin_config_dir.iterdir()) else "warning"
#         status_info.append(SystemStatus(
#             component="Configuration",
#             status=config_status,
#             details={
#                 "admin_config_dir": str(admin_config_dir),
#                 "files_count": len(list(admin_config_dir.glob("*"))) if admin_config_dir.exists() else 0
#             }
#         ))
        
#         # Check log file
#         from otel_config import get_otel_config
#         otel_cfg = get_otel_config()
#         log_file = otel_cfg.get_log_file_path() if otel_cfg else Path("logs/app.jsonl")
#         log_status = "healthy" if log_file.exists() else "warning"
#         status_info.append(SystemStatus(
#             component="Logging",
#             status=log_status,
#             details={
#                 "log_file": str(log_file),
#                 "exists": log_file.exists(),
#                 "size_bytes": log_file.stat().st_size if log_file.exists() else 0
#             }
#         ))
        
#         # Check MCP server health
#         mcp_health = get_mcp_health_status()
#         mcp_status = mcp_health.get("overall_status", "unknown")
#         status_info.append(SystemStatus(
#             component="MCP Servers",
#             status=mcp_status,
#             details={
#                 "healthy_count": mcp_health.get("healthy_count", 0),
#                 "total_count": mcp_health.get("total_count", 0),
#                 "last_check": mcp_health.get("last_check"),
#                 "check_interval": mcp_health.get("check_interval", 300)
#             }
#         ))
        
#         return {
#             "overall_status": "healthy" if all(s.status == "healthy" for s in status_info) else "warning",
#             "components": [s.model_dump() for s in status_info],
#             "checked_by": admin_user,
#             "timestamp": log_file.stat().st_mtime if log_file.exists() else None
#         }
#     except Exception as e:
#         logger.error(f"Error getting system status: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


# # --- Health Check Trigger ---

# @admin_router.get("/mcp-health")
# async def get_mcp_health(admin_user: str = Depends(require_admin)):
#     """Get detailed MCP server health information."""
#     try:
#         health_summary = get_mcp_health_status()
#         return {
#             "health_summary": health_summary,
#             "checked_by": admin_user
#         }
#     except Exception as e:
#         logger.error(f"Error getting MCP health: {e}")
#         raise HTTPException(status_code=500, detail=str(e))


# @admin_router.post("/trigger-health-check")
# async def trigger_health_check(admin_user: str = Depends(require_admin)):
#     """Manually trigger MCP server health checks."""
#     try:
#         # Try to get the MCP manager from main application state
#         mcp_manager = None
#         try:
#             from main import mcp_manager as main_mcp_manager
#             mcp_manager = main_mcp_manager
#         except ImportError:
#             # In test environment, mcp_manager might not be available
#             logger.warning("MCP manager not available for health check")
        
#         # Trigger health check
#         health_results = await trigger_mcp_health_check(mcp_manager)
        
#         # Get summary
#         health_summary = get_mcp_health_status()
        
#         logger.info(f"Health check triggered by {admin_user}")
#         return {
#             "message": "MCP server health check completed",
#             "triggered_by": admin_user,
#             "summary": health_summary,
#             "details": health_results
#         }
#     except Exception as e:
#         logger.error(f"Error triggering health check: {e}")
#         raise HTTPException(status_code=500, detail=f"Error triggering health check: {str(e)}")


# @admin_router.post("/reload-config")
# async def reload_configuration(admin_user: str = Depends(require_admin)):
#     """Reload configuration from configfilesadmin files."""
#     try:
#         # Reload configuration from files
#         config_manager.reload_configs()
        
#         # Validate the reloaded configurations
#         validation_status = config_manager.validate_config()
        
#         # Get the updated configurations for verification
#         llm_models = list(config_manager.llm_config.models.keys())
#         mcp_servers = list(config_manager.mcp_config.servers.keys())
        
#         logger.info(f"Configuration reloaded by {admin_user}")
#         logger.info(f"Reloaded LLM models: {llm_models}")
#         logger.info(f"Reloaded MCP servers: {mcp_servers}")
        
#         return {
#             "message": "Configuration reloaded successfully",
#             "reloaded_by": admin_user,
#             "validation_status": validation_status,
#             "llm_models_count": len(llm_models),
#             "mcp_servers_count": len(mcp_servers),
#             "llm_models": llm_models,
#             "mcp_servers": mcp_servers
#         }
#     except Exception as e:
#         logger.error(f"Error reloading config: {e}")
#         raise HTTPException(status_code=500, detail=f"Error reloading configuration: {str(e)}")