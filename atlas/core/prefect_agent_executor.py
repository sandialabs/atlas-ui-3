"""Prefect-based agent executor for ATLAS.

Creates and manages Prefect flows that run persistent AI agents.
Each agent launch creates a Prefect flow run with the agent's configuration,
MCP tool access, and sandbox constraints.
"""

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

PREFECT_API_URL = os.getenv("PREFECT_API_URL", "http://prefect-server:4200/api")
PREFECT_TIMEOUT = float(os.getenv("PREFECT_TIMEOUT", "10.0"))
PREFECT_WORK_POOL = os.getenv("PREFECT_WORK_POOL", "kubernetes-pool")

# K8s job template for agent execution in sandboxed containers
K8S_JOB_TEMPLATE = {
    "job_configuration": {
        "job_manifest": {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "namespace": "atlas",
                "labels": {
                    "app": "atlas-agent",
                },
            },
            "spec": {
                "ttlSecondsAfterFinished": 3600,
                "backoffLimit": 0,
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "atlas-agent",
                        },
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": "prefect-worker",
                        "containers": [
                            {
                                "name": "agent",
                                "image": "localhost/atlas-prefect-runner:latest",
                                "command": ["python", "-m", "atlas.main"],
                                "env": [
                                    {"name": "PREFECT_API_URL", "value": PREFECT_API_URL},
                                ],
                                "resources": {
                                    "requests": {"memory": "256Mi", "cpu": "100m"},
                                    "limits": {"memory": "1Gi", "cpu": "1000m"},
                                },
                            }
                        ],
                    },
                },
            },
        },
    },
}


class PrefectAgentExecutor:
    """Manages agent lifecycle through Prefect flows.

    Each agent template maps to a Prefect deployment. Launching an agent
    creates a flow run with parameters containing the agent configuration.
    """

    def __init__(self, api_url: str = PREFECT_API_URL):
        self.api_url = api_url.rstrip("/")
        self._available: Optional[bool] = None
        self._deployment_ids: Dict[str, str] = {}

    async def is_healthy(self) -> bool:
        """Check if Prefect server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.api_url}/health")
                healthy = resp.status_code == 200
                if healthy and not self._available:
                    logger.info("Prefect server available at %s", self.api_url)
                self._available = healthy
                return healthy
        except (httpx.RequestError, httpx.HTTPStatusError):
            if self._available is not False:
                logger.warning("Prefect server unreachable at %s", self.api_url)
            self._available = False
            return False

    async def ensure_flow(self, flow_name: str) -> Optional[str]:
        """Ensure a flow exists in Prefect, creating it if needed.

        Returns the flow ID.
        """
        try:
            async with httpx.AsyncClient(timeout=self.api_url and PREFECT_TIMEOUT) as client:
                # Check if flow exists
                resp = await client.post(
                    f"{self.api_url}/flows/filter",
                    json={"flows": {"name": {"any_": [flow_name]}}},
                )
                resp.raise_for_status()
                flows = resp.json()
                if flows:
                    return flows[0]["id"]

                # Create the flow
                resp = await client.post(
                    f"{self.api_url}/flows/",
                    json={"name": flow_name},
                )
                resp.raise_for_status()
                return resp.json()["id"]

        except Exception as exc:
            logger.error("Failed to ensure Prefect flow '%s': %s", flow_name, exc)
            return None

    async def create_deployment(
        self,
        flow_id: str,
        deployment_name: str,
        parameters: Dict[str, Any],
    ) -> Optional[str]:
        """Create a Prefect deployment for an agent template.

        Returns the deployment ID.
        """
        try:
            async with httpx.AsyncClient(timeout=PREFECT_TIMEOUT) as client:
                # Check if deployment already exists
                resp = await client.post(
                    f"{self.api_url}/deployments/filter",
                    json={
                        "deployments": {
                            "name": {"any_": [deployment_name]},
                        },
                        "flows": {"id": {"any_": [flow_id]}},
                    },
                )
                resp.raise_for_status()
                deployments = resp.json()
                if deployments:
                    dep_id = deployments[0]["id"]
                    self._deployment_ids[deployment_name] = dep_id
                    return dep_id

                # Create deployment targeting the kubernetes work pool
                resp = await client.post(
                    f"{self.api_url}/deployments/",
                    json={
                        "name": deployment_name,
                        "flow_id": flow_id,
                        "parameters": parameters,
                        "tags": ["atlas-agent"],
                        "work_pool_name": PREFECT_WORK_POOL,
                        "job_variables": {
                            "namespace": "atlas",
                            "image": "localhost/atlas-prefect-runner:latest",
                            "service_account_name": "prefect-worker",
                        },
                    },
                )
                resp.raise_for_status()
                dep_id = resp.json()["id"]
                self._deployment_ids[deployment_name] = dep_id
                return dep_id

        except Exception as exc:
            logger.error("Failed to create Prefect deployment '%s': %s", deployment_name, exc)
            return None

    async def launch_agent_flow(
        self,
        agent_config: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Launch an agent as a Prefect flow run.

        Creates (or reuses) a flow and deployment for the agent template,
        then triggers a flow run with the agent's specific parameters.

        Args:
            agent_config: The agent record from the agent registry.

        Returns:
            Dict with flow_run_id and status, or None on failure.
        """
        if not await self.is_healthy():
            logger.warning("Prefect server unavailable; agent will run in-memory only")
            return None

        template_id = agent_config.get("template_id", "custom")
        agent_id = agent_config["id"]
        flow_name = f"atlas-agent-{template_id}"
        deployment_name = f"atlas-agent-{template_id}-deployment"

        # Ensure flow exists
        flow_id = await self.ensure_flow(flow_name)
        if not flow_id:
            return None

        # Ensure deployment exists with template defaults
        dep_id = self._deployment_ids.get(deployment_name)
        if not dep_id:
            dep_id = await self.create_deployment(
                flow_id=flow_id,
                deployment_name=deployment_name,
                parameters={
                    "template_id": template_id,
                    "max_steps": agent_config.get("max_steps", 10),
                    "loop_strategy": agent_config.get("loop_strategy", "think-act"),
                    "mcp_servers": agent_config.get("mcp_servers", []),
                    "sandbox_policy": agent_config.get("sandbox_policy", "restrictive"),
                },
            )
        if not dep_id:
            return None

        # Create a flow run for this specific agent instance
        try:
            async with httpx.AsyncClient(timeout=PREFECT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.api_url}/deployments/{dep_id}/create_flow_run",
                    json={
                        "name": f"agent-{agent_id}",
                        "parameters": {
                            "agent_id": agent_id,
                            "owner": agent_config.get("owner", "unknown"),
                            "name": agent_config.get("name", f"Agent {agent_id}"),
                            "template_id": template_id,
                            "max_steps": agent_config.get("max_steps", 10),
                            "loop_strategy": agent_config.get("loop_strategy", "think-act"),
                            "mcp_servers": agent_config.get("mcp_servers", []),
                            "sandbox_policy": agent_config.get("sandbox_policy", "restrictive"),
                            "environment": agent_config.get("environment", {}),
                        },
                        "tags": ["atlas-agent", f"template:{template_id}", f"owner:{agent_config.get('owner', 'unknown')}"],
                    },
                )
                resp.raise_for_status()
                flow_run = resp.json()

                logger.info(
                    "Prefect flow run created: id=%s name=%s deployment=%s",
                    flow_run["id"],
                    flow_run.get("name"),
                    deployment_name,
                )

                return {
                    "flow_run_id": flow_run["id"],
                    "flow_run_name": flow_run.get("name"),
                    "deployment_id": dep_id,
                    "flow_id": flow_id,
                    "state": flow_run.get("state", {}).get("type", "SCHEDULED"),
                }

        except Exception as exc:
            logger.error("Failed to create Prefect flow run for agent %s: %s", agent_id, exc)
            return None

    async def get_flow_run_status(self, flow_run_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a Prefect flow run."""
        try:
            async with httpx.AsyncClient(timeout=PREFECT_TIMEOUT) as client:
                resp = await client.get(f"{self.api_url}/flow_runs/{flow_run_id}")
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "id": data["id"],
                        "name": data.get("name"),
                        "state_type": data.get("state", {}).get("type"),
                        "state_name": data.get("state", {}).get("name"),
                        "start_time": data.get("start_time"),
                        "end_time": data.get("end_time"),
                        "total_task_run_count": data.get("total_task_run_count", 0),
                        "tags": data.get("tags", []),
                    }
                return None
        except Exception as exc:
            logger.error("Failed to get flow run status %s: %s", flow_run_id, exc)
            return None

    async def cancel_flow_run(self, flow_run_id: str) -> bool:
        """Cancel a running Prefect flow run."""
        try:
            async with httpx.AsyncClient(timeout=PREFECT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.api_url}/flow_runs/{flow_run_id}/set_state",
                    json={
                        "state": {"type": "CANCELLING"},
                        "force": True,
                    },
                )
                if resp.status_code in (200, 201):
                    logger.info("Prefect flow run %s cancelled", flow_run_id)
                    return True
                logger.warning("Failed to cancel flow run %s: HTTP %d", flow_run_id, resp.status_code)
                return False
        except Exception as exc:
            logger.error("Failed to cancel flow run %s: %s", flow_run_id, exc)
            return False

    async def list_agent_flow_runs(
        self,
        owner: Optional[str] = None,
        template_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List Prefect flow runs for ATLAS agents."""
        try:
            tags_filter = ["atlas-agent"]
            if owner:
                tags_filter.append(f"owner:{owner}")
            if template_id:
                tags_filter.append(f"template:{template_id}")

            async with httpx.AsyncClient(timeout=PREFECT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.api_url}/flow_runs/filter",
                    json={
                        "flow_runs": {
                            "tags": {"all_": tags_filter},
                        },
                        "sort": "START_TIME_DESC",
                        "limit": limit,
                    },
                )
                resp.raise_for_status()
                runs = resp.json()

                return [
                    {
                        "id": r["id"],
                        "name": r.get("name"),
                        "state_type": r.get("state", {}).get("type"),
                        "state_name": r.get("state", {}).get("name"),
                        "start_time": r.get("start_time"),
                        "end_time": r.get("end_time"),
                        "parameters": r.get("parameters", {}),
                        "tags": r.get("tags", []),
                    }
                    for r in runs
                ]

        except Exception as exc:
            logger.error("Failed to list agent flow runs: %s", exc)
            return []

    async def get_prefect_info(self) -> Dict[str, Any]:
        """Get Prefect server info and agent-related stats."""
        healthy = await self.is_healthy()
        if not healthy:
            return {"healthy": False, "url": self.api_url}

        try:
            async with httpx.AsyncClient(timeout=PREFECT_TIMEOUT) as client:
                # Get agent flow runs count
                resp = await client.post(
                    f"{self.api_url}/flow_runs/count",
                    json={
                        "flow_runs": {
                            "tags": {"all_": ["atlas-agent"]},
                        },
                    },
                )
                agent_runs = resp.json() if resp.status_code == 200 else 0

                # Get flows count
                resp = await client.post(
                    f"{self.api_url}/flows/count",
                    json={},
                )
                total_flows = resp.json() if resp.status_code == 200 else 0

                return {
                    "healthy": True,
                    "url": self.api_url,
                    "agent_flow_runs": agent_runs,
                    "total_flows": total_flows,
                }

        except Exception as exc:
            logger.error("Failed to get Prefect info: %s", exc)
            return {"healthy": True, "url": self.api_url, "error": str(exc)}


# Module-level singleton
_prefect_executor: Optional[PrefectAgentExecutor] = None


def get_prefect_executor() -> PrefectAgentExecutor:
    """Get or create the module-level Prefect executor singleton."""
    global _prefect_executor
    if _prefect_executor is None:
        _prefect_executor = PrefectAgentExecutor()
    return _prefect_executor
