"""Egress allowlist resolution for Agent Portal V3 runs (Phase 0).

Computes, per run, the set of destinations an agent Job is permitted to reach
and resolves them to IP CIDRs that the per-run NetworkPolicy pins as `ipBlock`
allow rules (deny-by-default for everything else).

Policy model (admin sets the ceiling; users can never exceed it):
  * required_allowlist -- effective = LLM host + selected MCP hosts + admin
    allowlist. Users cannot widen it.
  * user_choice        -- additionally allows user-requested domains, but only
    those also present in the admin's user_allowlist_max set.
  * open               -- legacy public egress; no deny-by-default.

Phase 0 caveats (closed by the Phase 1 gateway / OpenShift EgressFirewall):
  * Domains are resolved to IPs *at launch*; rotating CDN IPs (e.g. the LLM
    APIs behind Cloudflare) can drift during a long run. Fine for short
    one-shot runs; the gateway removes this caveat.
  * Wildcards ("*.mycorp.internal") cannot be pinned to IPs and are reported
    as unenforceable here -- they need the FQDN-aware backend.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from atlas.modules.agent_portal_v3.job_template import _extract_host, _is_in_cluster_host, _llm_provider_hosts

# A resolver maps a hostname to a list of IP strings (injectable for tests).
Resolver = Callable[[str], List[str]]

VALID_MODES = ("required_allowlist", "user_choice", "open")


@dataclass
class EgressDecision:
    """Outcome of resolving a run's egress policy."""

    enabled: bool                       # allowlist feature switched on?
    mode: str                           # required_allowlist | user_choice | open
    deny_by_default: bool               # lock egress to the allowlist?
    domains: List[str] = field(default_factory=list)       # effective allowed domains
    cidrs: List[str] = field(default_factory=list)         # resolved IP CIDRs (/32) to pin
    unresolved: List[str] = field(default_factory=list)    # wildcards / DNS failures (not enforced in Phase 0)


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _default_resolver(host: str) -> List[str]:
    """Resolve a hostname to its IPv4 addresses (best effort)."""
    try:
        infos = socket.getaddrinfo(host, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError:
        return []
    ips = {info[4][0] for info in infos}
    return sorted(ips)


def _is_wildcard(domain: str) -> bool:
    return "*" in domain


def _external_mcp_hosts(mcp_resolved: Optional[Dict]) -> List[str]:
    """Hosts of selected MCP servers that are NOT in-cluster.

    In-cluster MCP servers are reached via the same-namespace egress allowance
    in build_network_policy, so they don't need an ipBlock entry here.
    """
    hosts: List[str] = []
    for cfg in (mcp_resolved or {}).values():
        url = cfg.get("url") if isinstance(cfg, dict) else None
        host = _extract_host(url)
        if host and not _is_in_cluster_host(host):
            hosts.append(host)
    return hosts


def resolve_egress(
    *,
    enabled: bool,
    mode: str,
    admin_allowlist: str,
    user_allowlist_max: str = "",
    user_requested: Optional[List[str]] = None,
    llm_provider: str,
    mcp_resolved: Optional[Dict] = None,
    resolver: Resolver = _default_resolver,
) -> EgressDecision:
    """Resolve the effective egress allowlist for a run.

    Returns an EgressDecision. When the feature is off or mode is "open",
    deny_by_default is False and the caller should keep the legacy NetworkPolicy.
    """
    mode = mode if mode in VALID_MODES else "required_allowlist"

    if not enabled or mode == "open":
        return EgressDecision(enabled=enabled, mode="open" if enabled else mode, deny_by_default=False)

    # Destinations the agent always needs to function.
    required = list(_llm_provider_hosts(llm_provider)) + _external_mcp_hosts(mcp_resolved)

    effective = set(required) | set(_split_csv(admin_allowlist))

    if mode == "user_choice" and user_requested:
        allowed_user = set(_split_csv(user_allowlist_max))
        effective |= (set(user_requested) & allowed_user)

    domains = sorted(effective)

    cidrs: List[str] = []
    unresolved: List[str] = []
    for domain in domains:
        if _is_wildcard(domain):
            unresolved.append(domain)
            continue
        ips = resolver(domain)
        if not ips:
            unresolved.append(domain)
            continue
        cidrs.extend(f"{ip}/32" for ip in ips)

    # De-dup CIDRs while preserving order.
    seen = set()
    deduped = [c for c in cidrs if not (c in seen or seen.add(c))]

    return EgressDecision(
        enabled=True,
        mode=mode,
        deny_by_default=True,
        domains=domains,
        cidrs=deduped,
        unresolved=unresolved,
    )


def egress_summary(*, enabled: bool, mode: str, admin_allowlist: str, user_allowlist_max: str) -> Dict:
    """Lightweight policy summary for the UI (no DNS resolution)."""
    eff_mode = mode if mode in VALID_MODES else "required_allowlist"
    return {
        "enabled": bool(enabled),
        "mode": eff_mode if enabled else "open",
        "deny_by_default": bool(enabled and eff_mode != "open"),
        "admin_allowlist": _split_csv(admin_allowlist),
        "user_editable": bool(enabled and eff_mode == "user_choice"),
        "user_allowlist_max": _split_csv(user_allowlist_max),
    }
