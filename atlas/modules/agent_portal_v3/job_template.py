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


def _is_in_cluster_host(host: Optional[str]) -> bool:
    """Heuristic: does this MCP host resolve to something inside the cluster?

    In-cluster Services are reached as bare names ("mcp-tools"), namespaced
    names ("mcp-tools.atlas"), or fully-qualified cluster DNS
    ("mcp-tools.atlas.svc.cluster.local"). Such names resolve to ClusterIP /
    pod IPs in the cluster's private range, which the public-egress rule below
    intentionally blocks -- so we need a dedicated allow rule for them.
    """
    if not host:
        return False
    h = host.strip().lower()
    if not h:
        return False
    # IP literals are handled by the public/private egress rules, not here.
    if all(part.isdigit() for part in h.split(".") if part):
        return False
    if h.endswith(".svc") or h.endswith(".svc.cluster.local") or h.endswith(".cluster.local"):
        return True
    # Bare service name (no dots) -- only resolvable via cluster DNS search.
    if "." not in h:
        return True
    return False


def _has_in_cluster_mcp(mcp_resolved: Optional[Dict[str, Any]]) -> bool:
    for cfg in (mcp_resolved or {}).values():
        url = cfg.get("url") if isinstance(cfg, dict) else None
        if _is_in_cluster_host(_extract_host(url)):
            return True
    return False


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
    deny_by_default: bool = False,
    allow_cidrs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Egress NetworkPolicy for an agent run.

    Two postures:

    - Legacy / open (deny_by_default=False): allow DNS + egress to public
      TCP/80/443, blocking RFC1918 / link-local via ipBlock except-clauses.
      Anything public is reachable; the agent is trusted to call only the
      configured hosts.

    - Allowlist (deny_by_default=True): deny everything except DNS, the
      resolved allowlist CIDRs (``allow_cidrs``) on TCP/80/443, and (if an
      in-cluster MCP is selected) same-namespace pods. This is the Phase 0
      enforcement -- domains are resolved to IPs by the egress resolver and
      pinned here. FQDN-accurate enforcement (rotating CDN IPs, wildcards)
      is the job of the Phase 1 egress gateway, or an OpenShift
      ``EgressFirewall`` with ``dnsName`` rules on OVN-Kubernetes.

    Enforcement notes: k3s (flannel + kube-router) and OpenShift 4.x
    (OVN-Kubernetes) both enforce L3/L4 NetworkPolicy egress incl. ipBlock.
    Older OpenShiftSDN did not enforce egress NetworkPolicy -- there, use
    ``EgressFirewall``/``EgressNetworkPolicy`` instead.
    """
    name = f"atlas-run-{run_id[:20]}"
    allow_ports = [{"protocol": "TCP", "port": 443}, {"protocol": "TCP", "port": 80}]

    # DNS is always required (resolving the LLM/MCP hosts, the gateway, etc.).
    dns_rule = {
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
    }

    if deny_by_default:
        egress: List[Dict[str, Any]] = [dns_rule]
        cidr_targets = [{"ipBlock": {"cidr": c}} for c in (allow_cidrs or [])]
        if cidr_targets:
            egress.append({"to": cidr_targets, "ports": allow_ports})
    else:
        egress = [
            dns_rule,
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

    # In-cluster MCP servers (e.g. a Service like mcp-tools.atlas.svc.cluster.local)
    # resolve to ClusterIP / pod IPs in the cluster's private range, which the
    # public-egress rule above intentionally blocks. When the run selects an
    # in-cluster MCP server, allow egress to pods in this namespace so the agent
    # can actually reach it. Same-namespace scope keeps the blast radius small;
    # for cross-namespace MCP, widen this selector or front it with a proxy.
    if _has_in_cluster_mcp(mcp_resolved):
        egress.append({"to": [{"podSelector": {}}]})

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
