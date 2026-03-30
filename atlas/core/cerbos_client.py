"""Cerbos policy engine client for fine-grained access control.

Provides async authorization checks against a Cerbos PDP instance.
Falls back to permissive mode when Cerbos is unavailable (dev/testing).
"""

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Cerbos PDP endpoint - defaults to k3s service address
CERBOS_URL = os.getenv("CERBOS_URL", "http://cerbos.atlas.svc.cluster.local:3592")
CERBOS_TIMEOUT = float(os.getenv("CERBOS_TIMEOUT", "5.0"))


class CerbosClient:
    """Async client for Cerbos policy decision point (PDP).

    Checks authorization decisions for resources (agents, MCP tools, HPC jobs,
    data sources) against Cerbos policies. Falls back to the existing group-based
    RBAC when Cerbos is unreachable.
    """

    def __init__(self, base_url: str = CERBOS_URL, timeout: float = CERBOS_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._available: Optional[bool] = None

    async def is_healthy(self) -> bool:
        """Check if the Cerbos PDP is reachable."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{self.base_url}/_cerbos/health")
                healthy = resp.status_code == 200
                if healthy and not self._available:
                    logger.info("Cerbos PDP is available at %s", self.base_url)
                self._available = healthy
                return healthy
        except (httpx.RequestError, httpx.HTTPStatusError):
            if self._available is not False:
                logger.warning("Cerbos PDP unreachable at %s; falling back to group RBAC", self.base_url)
            self._available = False
            return False

    async def check(
        self,
        principal_id: str,
        principal_roles: List[str],
        principal_attrs: Dict[str, Any],
        resource_kind: str,
        resource_id: str,
        resource_attrs: Dict[str, Any],
        actions: List[str],
    ) -> Dict[str, bool]:
        """Check authorization for a set of actions on a resource.

        Returns a dict mapping each action to True (allowed) or False (denied).
        If Cerbos is unavailable, returns all actions as allowed (fail-open for dev).
        """
        if self._available is None:
            await self.is_healthy()

        if not self._available:
            # Fail-open in dev; production should set CERBOS_FAIL_CLOSED=true
            fail_closed = os.getenv("CERBOS_FAIL_CLOSED", "false").lower() in ("true", "1")
            if fail_closed:
                logger.warning("Cerbos unavailable and CERBOS_FAIL_CLOSED=true; denying all actions")
                return {action: False for action in actions}
            return {action: True for action in actions}

        payload = {
            "requestId": f"{principal_id}:{resource_kind}:{resource_id}",
            "principal": {
                "id": principal_id,
                "roles": principal_roles,
                "attr": principal_attrs,
            },
            "resources": [
                {
                    "resource": {
                        "kind": resource_kind,
                        "id": resource_id,
                        "attr": resource_attrs,
                    },
                    "actions": actions,
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/check/resources",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            # Parse Cerbos v2 response: results[].actions[action] == "EFFECT_ALLOW"
            results = {}
            resource_results = data.get("results", [])
            action_effects = {}
            if isinstance(resource_results, list) and resource_results:
                action_effects = resource_results[0].get("actions", {})

            for action in actions:
                effect = action_effects.get(action, "EFFECT_DENY")
                results[action] = effect == "EFFECT_ALLOW"

            return results

        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.error("Cerbos check failed: %s", exc)
            self._available = False
            fail_closed = os.getenv("CERBOS_FAIL_CLOSED", "false").lower() in ("true", "1")
            if fail_closed:
                return {action: False for action in actions}
            return {action: True for action in actions}

    async def check_action(
        self,
        principal_id: str,
        principal_roles: List[str],
        principal_attrs: Dict[str, Any],
        resource_kind: str,
        resource_id: str,
        resource_attrs: Dict[str, Any],
        action: str,
    ) -> bool:
        """Convenience: check a single action, return True/False."""
        results = await self.check(
            principal_id=principal_id,
            principal_roles=principal_roles,
            principal_attrs=principal_attrs,
            resource_kind=resource_kind,
            resource_id=resource_id,
            resource_attrs=resource_attrs,
            actions=[action],
        )
        return results.get(action, False)

    async def check_batch(
        self,
        principal_id: str,
        principal_roles: List[str],
        principal_attrs: Dict[str, Any],
        checks: List[Dict[str, Any]],
    ) -> List[Dict[str, bool]]:
        """Check multiple resources in one call.

        Each item in checks should have: resource_kind, resource_id, resource_attrs, actions.
        Returns a list of action->bool dicts in the same order.
        """
        results = []
        for item in checks:
            result = await self.check(
                principal_id=principal_id,
                principal_roles=principal_roles,
                principal_attrs=principal_attrs,
                resource_kind=item["resource_kind"],
                resource_id=item["resource_id"],
                resource_attrs=item.get("resource_attrs", {}),
                actions=item["actions"],
            )
            results.append(result)
        return results


# Module-level singleton
_cerbos_client: Optional[CerbosClient] = None


def get_cerbos_client() -> CerbosClient:
    """Get or create the module-level Cerbos client singleton."""
    global _cerbos_client
    if _cerbos_client is None:
        _cerbos_client = CerbosClient()
    return _cerbos_client
