"""Agent management routes for the ATLAS agent portal.

Provides endpoints to register, launch, monitor, and stop persistent AI agents.
Integrates with Cerbos for fine-grained authorization, Keycloak for token
management, and Prefect for flow orchestration. Agent state persists in
PostgreSQL via the AgentStore.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from atlas.core.agent_store import get_agent_store
from atlas.core.auth import is_user_in_group
from atlas.core.cerbos_client import get_cerbos_client
from atlas.core.keycloak_client import get_keycloak_client
from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging
from atlas.core.prefect_agent_executor import get_prefect_executor
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config import config_manager

logger = logging.getLogger(__name__)

agent_router = APIRouter(prefix="/api/agents", tags=["agents"])


# Pre-defined agent templates that are approved for all users
AGENT_TEMPLATES = {
    "code-review": {
        "name": "Code Review Agent",
        "description": "Reviews code for security vulnerabilities, style issues, and best practices",
        "mcp_servers": ["filesystem", "code-executor"],
        "max_steps": 10,
        "loop_strategy": "think-act",
        "sandbox_policy": "restrictive",
        "template_approved": True,
    },
    "data-analysis": {
        "name": "Data Analysis Agent",
        "description": "Analyzes datasets, generates visualizations, and produces summary reports",
        "mcp_servers": ["csv_reporter", "basictable"],
        "max_steps": 15,
        "loop_strategy": "react",
        "sandbox_policy": "standard",
        "template_approved": True,
    },
    "document-research": {
        "name": "Document Research Agent",
        "description": "Searches and summarizes documents from RAG stores with citations",
        "mcp_servers": ["pdfbasic"],
        "max_steps": 20,
        "loop_strategy": "agentic",
        "sandbox_policy": "standard",
        "template_approved": True,
    },
    "hpc-job-manager": {
        "name": "HPC Job Manager Agent",
        "description": "Submits, monitors, and manages jobs on HPC clusters via SLURM",
        "mcp_servers": ["k3s_job_runner"],
        "max_steps": 30,
        "loop_strategy": "react",
        "sandbox_policy": "hpc",
        "template_approved": True,
    },
    "custom": {
        "name": "Custom Agent",
        "description": "User-defined agent with custom tool selection and configuration",
        "mcp_servers": [],
        "max_steps": 10,
        "loop_strategy": "think-act",
        "sandbox_policy": "restrictive",
        "template_approved": False,
    },
}


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class AgentLaunchRequest(BaseModel):
    """Request to launch a new persistent agent."""
    template_id: str = Field(..., description="Agent template to use")
    name: Optional[str] = Field(None, description="Custom name for this agent instance")
    mcp_servers: Optional[List[str]] = Field(None, description="Override MCP servers (custom template only)")
    max_steps: Optional[int] = Field(None, ge=1, le=100, description="Max agent loop iterations")
    loop_strategy: Optional[str] = Field(None, description="Agent loop strategy override")
    sandbox_policy: Optional[str] = Field(None, description="Sandbox enforcement policy")
    environment: Optional[Dict[str, str]] = Field(None, description="Environment variables for the agent")


class AgentStopRequest(BaseModel):
    """Request to stop a running agent."""
    agent_id: str
    force: bool = False


class AgentConfigUpdate(BaseModel):
    """Request to update agent configuration."""
    max_steps: Optional[int] = Field(None, ge=1, le=100)
    sandbox_policy: Optional[str] = None
    mcp_servers: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Helper: resolve user roles for Cerbos
# ---------------------------------------------------------------------------

async def _get_user_roles(user_email: str, request: Optional[Request] = None) -> List[str]:
    """Map user to Cerbos roles. Uses Keycloak claims if available, else group RBAC."""
    # Check for Keycloak claims on request (set by middleware)
    if request and hasattr(request.state, "keycloak_claims"):
        return request.state.keycloak_claims.get("roles", ["viewer"])

    roles = []
    admin_group = config_manager.app_settings.admin_group
    if await is_user_in_group(user_email, admin_group):
        roles.append("admin")
    if await is_user_in_group(user_email, "operators"):
        roles.append("operator")
    if await is_user_in_group(user_email, "users"):
        roles.append("user")
    # Authenticated users without explicit group membership get "user" baseline
    # (they passed login — they're not anonymous viewers)
    if not roles:
        roles.append("user")
    return roles


async def _get_user_attrs(user_email: str) -> Dict[str, Any]:
    """Build principal attributes for Cerbos from the user's profile."""
    mcp_manager = app_factory.get_mcp_manager()
    authorized_servers = await mcp_manager.get_authorized_servers(user_email, is_user_in_group)

    return {
        "authorized_servers": authorized_servers,
        "allowed_compliance_levels": ["Public", "Internal", "Controlled"],
        "allowed_queues": ["default", "gpu", "sandbox"],
        "clearance_levels": ["unclassified", "cui"],
        "whitelisted_sources": [],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@agent_router.get("/templates")
async def list_agent_templates(
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """List available agent templates."""
    cerbos = get_cerbos_client()
    roles = await _get_user_roles(current_user, request)
    attrs = await _get_user_attrs(current_user)

    templates = []
    for template_id, template in AGENT_TEMPLATES.items():
        allowed = await cerbos.check_action(
            principal_id=current_user,
            principal_roles=roles,
            principal_attrs=attrs,
            resource_kind="agent",
            resource_id=template_id,
            resource_attrs={
                "owner": current_user,
                "template_approved": template["template_approved"],
            },
            action="launch",
        )
        templates.append({
            "id": template_id,
            **template,
            "can_launch": allowed,
        })

    return {"templates": templates}


@agent_router.post("/launch")
async def launch_agent(
    launch_request: AgentLaunchRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Launch a new persistent agent instance."""
    template = AGENT_TEMPLATES.get(launch_request.template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template '{launch_request.template_id}' not found")

    # Cerbos authorization check
    cerbos = get_cerbos_client()
    roles = await _get_user_roles(current_user, request)
    attrs = await _get_user_attrs(current_user)

    allowed = await cerbos.check_action(
        principal_id=current_user,
        principal_roles=roles,
        principal_attrs=attrs,
        resource_kind="agent",
        resource_id=launch_request.template_id,
        resource_attrs={
            "owner": current_user,
            "template_approved": template["template_approved"],
        },
        action="launch",
    )

    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to launch this agent type",
        )

    # Validate MCP servers if custom
    mcp_servers = launch_request.mcp_servers or template["mcp_servers"]
    if mcp_servers:
        mcp_manager = app_factory.get_mcp_manager()
        authorized = await mcp_manager.get_authorized_servers(current_user, is_user_in_group)
        unauthorized = [s for s in mcp_servers if s not in authorized]
        if unauthorized:
            raise HTTPException(
                status_code=403,
                detail=f"Not authorized for MCP servers: {unauthorized}",
            )

    # Create agent instance
    agent_id = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()
    agent_record = {
        "id": agent_id,
        "template_id": launch_request.template_id,
        "name": launch_request.name or f"{template['name']} ({agent_id})",
        "description": template["description"],
        "owner": current_user,
        "status": "running",
        "mcp_servers": mcp_servers,
        "max_steps": launch_request.max_steps or template["max_steps"],
        "loop_strategy": launch_request.loop_strategy or template["loop_strategy"],
        "sandbox_policy": launch_request.sandbox_policy or template["sandbox_policy"],
        "created_at": now,
        "started_at": now,
        "last_activity": now,
        "steps_completed": 0,
        "actions_log": [],
        "environment": launch_request.environment or {},
    }

    # Launch as a Prefect flow run
    prefect = get_prefect_executor()
    prefect_result = await prefect.launch_agent_flow(agent_record)
    if prefect_result:
        agent_record["prefect"] = prefect_result
        agent_record["status"] = "scheduled"
    else:
        agent_record["prefect"] = None

    # Request a scoped token from Keycloak for the agent (if available)
    keycloak = get_keycloak_client()
    if keycloak.enabled:
        auth_header = request.headers.get("Authorization", "")
        user_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        agent_token = await keycloak.exchange_token_for_agent(
            user_token=user_token,
            agent_id=agent_id,
        )
        agent_record["has_token"] = bool(agent_token)
    else:
        agent_record["has_token"] = False

    # Persist to database
    store = await get_agent_store()
    await store.save(agent_record)

    logger.info(
        "Agent launched: id=%s template=%s user=%s prefect=%s",
        agent_id,
        sanitize_for_logging(launch_request.template_id),
        sanitize_for_logging(current_user),
        bool(prefect_result),
    )

    return {
        "message": f"Agent '{agent_record['name']}' launched",
        "agent": agent_record,
    }


@agent_router.get("/")
async def list_agents(
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """List all agents visible to the current user."""
    cerbos = get_cerbos_client()
    roles = await _get_user_roles(current_user, request)
    attrs = await _get_user_attrs(current_user)

    allowed = await cerbos.check_action(
        principal_id=current_user,
        principal_roles=roles,
        principal_attrs=attrs,
        resource_kind="agent",
        resource_id="*",
        resource_attrs={"owner": current_user, "template_approved": True},
        action="list",
    )

    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorized to list agents")

    is_admin = "admin" in roles
    store = await get_agent_store()
    agents = await store.list_agents(owner=current_user, is_admin=is_admin)

    return {"agents": agents, "total": len(agents)}


@agent_router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Get detailed status for a specific agent."""
    store = await get_agent_store()
    agent = await store.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    cerbos = get_cerbos_client()
    roles = await _get_user_roles(current_user, request)
    attrs = await _get_user_attrs(current_user)

    allowed = await cerbos.check_action(
        principal_id=current_user,
        principal_roles=roles,
        principal_attrs=attrs,
        resource_kind="agent",
        resource_id=agent_id,
        resource_attrs={"owner": agent["owner"], "template_approved": True},
        action="monitor",
    )

    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorized to view this agent")

    action_checks = await cerbos.check(
        principal_id=current_user,
        principal_roles=roles,
        principal_attrs=attrs,
        resource_kind="agent",
        resource_id=agent_id,
        resource_attrs={"owner": agent["owner"], "template_approved": True},
        actions=["stop", "configure", "delete", "view_logs"],
    )

    return {
        "agent": agent,
        "permissions": action_checks,
    }


@agent_router.post("/{agent_id}/stop")
async def stop_agent(
    agent_id: str,
    stop_request: AgentStopRequest,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Stop a running agent."""
    store = await get_agent_store()
    agent = await store.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    cerbos = get_cerbos_client()
    roles = await _get_user_roles(current_user, request)
    attrs = await _get_user_attrs(current_user)

    allowed = await cerbos.check_action(
        principal_id=current_user,
        principal_roles=roles,
        principal_attrs=attrs,
        resource_kind="agent",
        resource_id=agent_id,
        resource_attrs={"owner": agent["owner"], "template_approved": True},
        action="stop",
    )

    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorized to stop this agent")

    # Cancel Prefect flow run if one exists
    prefect_info = agent.get("prefect")
    if prefect_info and prefect_info.get("flow_run_id"):
        prefect = get_prefect_executor()
        await prefect.cancel_flow_run(prefect_info["flow_run_id"])

    agent["status"] = "stopped"
    agent["stopped_at"] = datetime.now(timezone.utc).isoformat()
    agent["stopped_by"] = current_user
    await store.save(agent)

    logger.info(
        "Agent stopped: id=%s user=%s force=%s",
        agent_id,
        sanitize_for_logging(current_user),
        stop_request.force,
    )

    return {"message": f"Agent '{agent['name']}' stopped", "agent": agent}


@agent_router.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Delete an agent instance."""
    store = await get_agent_store()
    agent = await store.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    cerbos = get_cerbos_client()
    roles = await _get_user_roles(current_user, request)
    attrs = await _get_user_attrs(current_user)

    allowed = await cerbos.check_action(
        principal_id=current_user,
        principal_roles=roles,
        principal_attrs=attrs,
        resource_kind="agent",
        resource_id=agent_id,
        resource_attrs={"owner": agent["owner"], "template_approved": True},
        action="delete",
    )

    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorized to delete this agent")

    await store.delete(agent_id)

    logger.info(
        "Agent deleted: id=%s user=%s",
        agent_id,
        sanitize_for_logging(current_user),
    )

    return {"message": f"Agent '{agent['name']}' deleted"}


@agent_router.get("/{agent_id}/logs")
async def get_agent_logs(
    agent_id: str,
    request: Request,
    current_user: str = Depends(get_current_user),
):
    """Get action logs for an agent."""
    store = await get_agent_store()
    agent = await store.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    cerbos = get_cerbos_client()
    roles = await _get_user_roles(current_user, request)
    attrs = await _get_user_attrs(current_user)

    allowed = await cerbos.check_action(
        principal_id=current_user,
        principal_roles=roles,
        principal_attrs=attrs,
        resource_kind="agent",
        resource_id=agent_id,
        resource_attrs={"owner": agent["owner"], "template_approved": True},
        action="view_logs",
    )

    if not allowed:
        raise HTTPException(status_code=403, detail="Not authorized to view agent logs")

    return {
        "agent_id": agent_id,
        "agent_name": agent["name"],
        "logs": agent.get("actions_log", []),
    }


@agent_router.get("/cerbos/status")
async def cerbos_status(current_user: str = Depends(get_current_user)):
    """Check Cerbos PDP health and policy status (admin only)."""
    admin_group = config_manager.app_settings.admin_group
    if not await is_user_in_group(current_user, admin_group):
        raise HTTPException(status_code=403, detail="Admin access required")

    cerbos = get_cerbos_client()
    healthy = await cerbos.is_healthy()

    return {
        "cerbos_url": cerbos.base_url,
        "healthy": healthy,
        "fail_closed": (
            healthy is False
            and config_manager.app_settings.__dict__.get("cerbos_fail_closed", False)
        ),
    }


@agent_router.get("/prefect/status")
async def prefect_status(current_user: str = Depends(get_current_user)):
    """Get Prefect server status and agent flow run info (admin only)."""
    admin_group = config_manager.app_settings.admin_group
    if not await is_user_in_group(current_user, admin_group):
        raise HTTPException(status_code=403, detail="Admin access required")

    prefect = get_prefect_executor()
    info = await prefect.get_prefect_info()
    runs = await prefect.list_agent_flow_runs(limit=10)

    return {
        "prefect": info,
        "recent_runs": runs,
    }


@agent_router.get("/keycloak/status")
async def keycloak_status(current_user: str = Depends(get_current_user)):
    """Get Keycloak IAM status (admin only)."""
    admin_group = config_manager.app_settings.admin_group
    if not await is_user_in_group(current_user, admin_group):
        raise HTTPException(status_code=403, detail="Admin access required")

    keycloak = get_keycloak_client()
    healthy = await keycloak.is_healthy() if keycloak.enabled else False

    return {
        "enabled": keycloak.enabled,
        "healthy": healthy,
        "realm_url": f"{os.getenv('KEYCLOAK_URL', 'http://keycloak:8080/auth')}/realms/{os.getenv('KEYCLOAK_REALM', 'atlas')}",
    }


@agent_router.get("/infrastructure/status")
async def infrastructure_status(current_user: str = Depends(get_current_user)):
    """Combined status of all agent portal infrastructure (admin only)."""
    admin_group = config_manager.app_settings.admin_group
    if not await is_user_in_group(current_user, admin_group):
        raise HTTPException(status_code=403, detail="Admin access required")

    cerbos = get_cerbos_client()
    prefect = get_prefect_executor()
    keycloak = get_keycloak_client()

    cerbos_healthy = await cerbos.is_healthy()
    prefect_info = await prefect.get_prefect_info()
    keycloak_healthy = await keycloak.is_healthy() if keycloak.enabled else None

    store = await get_agent_store()
    all_agents = await store.list_agents(is_admin=True)

    return {
        "cerbos": {
            "healthy": cerbos_healthy,
            "url": cerbos.base_url,
        },
        "prefect": {
            "healthy": prefect_info.get("healthy", False),
            "url": prefect.api_url,
            "agent_flow_runs": prefect_info.get("agent_flow_runs", 0),
        },
        "keycloak": {
            "enabled": keycloak.enabled,
            "healthy": keycloak_healthy,
        },
        "agents": {
            "total": len(all_agents),
            "running": sum(1 for a in all_agents if a.get("status") == "running"),
            "scheduled": sum(1 for a in all_agents if a.get("status") == "scheduled"),
            "stopped": sum(1 for a in all_agents if a.get("status") == "stopped"),
        },
    }
