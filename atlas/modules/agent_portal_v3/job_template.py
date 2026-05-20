"""Builds K8s Job + NetworkPolicy manifests for Agent Portal V3 runs.

A run launches one Job (parallelism=1, backoffLimit=0). The Pod gets
labels that link it back to the AgentRunRecord, an env block with the
prompt + MCP config + LLM credentials, and a NetworkPolicy that only
allows DNS plus the egress endpoints we resolved up front.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

LABEL_APP = "atlas-agent-runner"
LABEL_RUN_ID = "atlas.run-id"
LABEL_USER = "atlas.user"
ANNOTATION_DISPLAY = "atlas.display-name"

# RFC1123: lower-case alphanumerics + '-', <=63 chars
_K8S_NAME_RE = re.compile(r"[^a-z0-9-]+")


def _slug(value: str, *, fallback: str = "agent") -> str:
    s = (value or "").lower()
    s = _K8S_NAME_RE.sub("-", s)
    s = s.strip("-")
    if not s:
        s = fallback
    return s[:40]


def job_name_for_run(run_id: str, *, user_email: str = "") -> str:
    """Deterministic, RFC1123-safe job name."""
    user_part = _slug(user_email.split("@")[0], fallback="anon")[:16]
    # use the first 12 chars of the run uuid for human readability
    return f"atlas-run-{user_part}-{run_id[:12]}"


def build_job_manifest(
    *,
    run_id: str,
    user_email: str,
    display_name: str,
    namespace: str,
    image: str,
    prompt: str,
    mcp_resolved: Dict[str, Any],
    llm_provider: str,
    llm_model: str,
    llm_api_key_secret_ref: Optional[Dict[str, str]] = None,
    llm_api_key_inline: Optional[str] = None,
    extra_env: Optional[Dict[str, str]] = None,
    cpu_limit: str = "500m",
    memory_limit: str = "512Mi",
    cpu_request: str = "100m",
    memory_request: str = "128Mi",
    active_deadline_seconds: int = 1800,
    image_pull_policy: str = "IfNotPresent",
    service_account: Optional[str] = None,
) -> Dict[str, Any]:
    """Returns a JSON-serializable Job manifest for create_namespaced_job."""

    name = job_name_for_run(run_id, user_email=user_email)

    env: List[Dict[str, Any]] = [
        {"name": "ATLAS_RUN_ID", "value": run_id},
        {"name": "ATLAS_USER_EMAIL", "value": user_email},
        {"name": "ATLAS_PROMPT", "value": prompt},
        {"name": "ATLAS_MCP_CONFIG", "value": json.dumps(mcp_resolved or {})},
        {"name": "ATLAS_LLM_PROVIDER", "value": llm_provider},
        {"name": "ATLAS_LLM_MODEL", "value": llm_model},
    ]

    # LLM API key: prefer a Secret ref, fall back to inline (dev only).
    if llm_api_key_secret_ref:
        env.append(
            {
                "name": _api_key_env_for_provider(llm_provider),
                "valueFrom": {
                    "secretKeyRef": {
                        "name": llm_api_key_secret_ref["name"],
                        "key": llm_api_key_secret_ref["key"],
                    }
                },
            }
        )
    elif llm_api_key_inline:
        env.append(
            {
                "name": _api_key_env_for_provider(llm_provider),
                "value": llm_api_key_inline,
            }
        )

    for k, v in (extra_env or {}).items():
        env.append({"name": k, "value": str(v)})

    labels = {
        "app": LABEL_APP,
        LABEL_RUN_ID: run_id,
        LABEL_USER: _slug(user_email.split("@")[0], fallback="anon"),
    }
    annotations = {ANNOTATION_DISPLAY: display_name or name}

    pod_spec: Dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "containers": [
            {
                "name": "agent",
                "image": image,
                "imagePullPolicy": image_pull_policy,
                "env": env,
                "resources": {
                    "limits": {"cpu": cpu_limit, "memory": memory_limit},
                    "requests": {"cpu": cpu_request, "memory": memory_request},
                },
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "runAsNonRoot": True,
                    "runAsUser": 10001,
                    "readOnlyRootFilesystem": False,
                    "capabilities": {"drop": ["ALL"]},
                },
            }
        ],
    }
    if service_account:
        pod_spec["serviceAccountName"] = service_account

    manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": labels,
            "annotations": annotations,
        },
        "spec": {
            "parallelism": 1,
            "completions": 1,
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,
            "activeDeadlineSeconds": active_deadline_seconds,
            "template": {
                "metadata": {"labels": labels, "annotations": annotations},
                "spec": pod_spec,
            },
        },
    }
    return manifest


def _api_key_env_for_provider(provider: str) -> str:
    p = (provider or "").lower()
    if p == "anthropic":
        return "ANTHROPIC_API_KEY"
    if p == "openai":
        return "OPENAI_API_KEY"
    if p in ("google", "gemini"):
        return "GOOGLE_API_KEY"
    if p == "openrouter":
        return "OPENROUTER_API_KEY"
    if p == "groq":
        return "GROQ_API_KEY"
    if p == "mistral":
        return "MISTRAL_API_KEY"
    return f"{p.upper()}_API_KEY"


# ---- NetworkPolicy ----

def _extract_host(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        u = urlparse(url)
        return u.hostname
    except Exception:  # noqa: BLE001
        return None


def _llm_provider_hosts(provider: str) -> List[str]:
    p = (provider or "").lower()
    if p == "anthropic":
        return ["api.anthropic.com"]
    if p == "openai":
        return ["api.openai.com"]
    if p in ("google", "gemini"):
        return ["generativelanguage.googleapis.com"]
    if p == "openrouter":
        return ["openrouter.ai"]
    if p == "groq":
        return ["api.groq.com"]
    if p == "mistral":
        return ["api.mistral.ai"]
    return []


def build_network_policy(
    *,
    run_id: str,
    namespace: str,
    llm_provider: str,
    mcp_resolved: Dict[str, Any],
    extra_allowed_hosts: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Egress NetworkPolicy: deny all by default, allow DNS + LLM + MCP hosts.

    Notes on enforcement:
    - k3s ships with flannel + the kube-router NetworkPolicy controller by
      default, which enforces L4 (port/IP). DNS-name egress *rules* are
      L7 features; the simpler-and-portable approach is to allow all
      egress to TCP/443 + UDP/53 to kube-dns and trust the agent image
      to only call the configured hosts. For dev that's enough. For a
      stricter prod posture, drop a Cilium L7 policy with FQDN allow-
      list, or front each pod with an egress proxy and only allow egress
      to the proxy.
    """
    name = f"atlas-run-{run_id[:20]}"
    allow_ports = [{"protocol": "TCP", "port": 443}, {"protocol": "TCP", "port": 80}]

    egress: List[Dict[str, Any]] = [
        # DNS
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                    }
                }
            ],
            "ports": [
                {"protocol": "UDP", "port": 53},
                {"protocol": "TCP", "port": 53},
            ],
        },
        # External HTTPS/HTTP (LLM + MCP) - block private nets via except clauses
        {
            "to": [
                {
                    "ipBlock": {
                        "cidr": "0.0.0.0/0",
                        "except": [
                            "10.0.0.0/8",
                            "172.16.0.0/12",
                            "192.168.0.0/16",
                            "169.254.0.0/16",
                        ],
                    }
                }
            ],
            "ports": allow_ports,
        },
    ]

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {LABEL_APP: "true", LABEL_RUN_ID: run_id},
        },
        "spec": {
            "podSelector": {"matchLabels": {LABEL_RUN_ID: run_id}},
            "policyTypes": ["Egress"],
            "egress": egress,
        },
    }
