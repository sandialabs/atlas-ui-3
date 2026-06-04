"""Tests for Agent Portal V3 Job + NetworkPolicy manifest builders.

Focus: the egress policy must allow an in-cluster MCP server (which resolves
to a ClusterIP in the cluster's private range and would otherwise be blocked by
the RFC1918 except-clause), while NOT loosening egress when every selected MCP
server is public.
"""

from __future__ import annotations

from atlas.modules.agent_portal_v3.egress import egress_summary, resolve_egress
from atlas.modules.agent_portal_v3.job_template import (
    _has_in_cluster_mcp,
    _is_in_cluster_host,
    build_job_manifest,
    build_network_policy,
)

# A deterministic fake resolver so tests never touch real DNS.
_FAKE_DNS = {
    "api.anthropic.com": ["1.2.3.4", "1.2.3.5"],
    "pypi.org": ["9.9.9.9"],
    "mcp.example.com": ["8.8.4.4"],
}


def _fake_resolver(host):
    return _FAKE_DNS.get(host, [])


def _has_pod_selector(netpol: dict) -> bool:
    return any(
        "podSelector" in (to or {})
        for rule in netpol["spec"]["egress"]
        for to in rule.get("to", [])
    )


def test_is_in_cluster_host():
    assert _is_in_cluster_host("mcp-tools.atlas.svc.cluster.local")
    assert _is_in_cluster_host("mcp-tools.atlas.svc")
    assert _is_in_cluster_host("mcp-tools")  # bare service name
    assert not _is_in_cluster_host("api.anthropic.com")
    assert not _is_in_cluster_host("example.com")
    assert not _is_in_cluster_host("192.168.1.10")
    assert not _is_in_cluster_host("10.43.0.1")
    assert not _is_in_cluster_host(None)
    assert not _is_in_cluster_host("")


def test_has_in_cluster_mcp():
    in_cluster = {"demo": {"transport": "http", "url": "http://mcp-tools.atlas.svc.cluster.local/mcp"}}
    public = {"remote": {"transport": "http", "url": "https://mcp.example.com/mcp"}}
    assert _has_in_cluster_mcp(in_cluster)
    assert not _has_in_cluster_mcp(public)
    assert not _has_in_cluster_mcp({})
    assert not _has_in_cluster_mcp(None)


def test_network_policy_allows_in_cluster_mcp_egress():
    netpol = build_network_policy(
        run_id="11111111-2222-3333-4444-555555555555",
        namespace="atlas",
        llm_provider="anthropic",
        mcp_resolved={"demo": {"transport": "http", "url": "http://mcp-tools.atlas.svc.cluster.local/mcp"}},
    )
    assert _has_pod_selector(netpol), "in-cluster MCP run should permit same-namespace egress"
    # DNS + public egress rules must still be present.
    assert len(netpol["spec"]["egress"]) == 3
    assert netpol["spec"]["policyTypes"] == ["Egress"]


def test_network_policy_no_extra_egress_for_public_mcp_only():
    netpol = build_network_policy(
        run_id="11111111-2222-3333-4444-555555555555",
        namespace="atlas",
        llm_provider="anthropic",
        mcp_resolved={"remote": {"transport": "http", "url": "https://mcp.example.com/mcp"}},
    )
    assert not _has_pod_selector(netpol), "public-only MCP must not widen egress to the cluster"
    assert len(netpol["spec"]["egress"]) == 2


def test_network_policy_no_extra_egress_without_mcp():
    netpol = build_network_policy(
        run_id="11111111-2222-3333-4444-555555555555",
        namespace="atlas",
        llm_provider="anthropic",
        mcp_resolved={},
    )
    assert not _has_pod_selector(netpol)
    assert len(netpol["spec"]["egress"]) == 2


def test_job_manifest_carries_mcp_config_and_key():
    manifest = build_job_manifest(
        run_id="abcdef01-2345-6789-abcd-ef0123456789",
        user_email="user@example.com",
        display_name="demo",
        namespace="atlas",
        image="localhost/atlas-agent-runner:dev",
        prompt="hello",
        mcp_resolved={"demo": {"transport": "http", "url": "http://mcp-tools.atlas.svc.cluster.local/mcp"}},
        llm_provider="anthropic",
        llm_model="claude-haiku-4-5",
        llm_api_key_inline="sk-test",
    )
    env = {e["name"]: e.get("value") for e in manifest["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert "mcp-tools.atlas.svc.cluster.local" in env["ATLAS_MCP_CONFIG"]
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["ATLAS_LLM_MODEL"] == "claude-haiku-4-5"
    # one-shot, no-retry job
    assert manifest["spec"]["backoffLimit"] == 0


# ---- egress allowlist resolver ----

def _cidrs(decision):
    return set(decision.cidrs)


def test_egress_disabled_keeps_open_posture():
    d = resolve_egress(
        enabled=False, mode="required_allowlist", admin_allowlist="pypi.org",
        llm_provider="anthropic", mcp_resolved={}, resolver=_fake_resolver,
    )
    assert d.deny_by_default is False


def test_egress_required_includes_llm_admin_and_external_mcp():
    d = resolve_egress(
        enabled=True, mode="required_allowlist", admin_allowlist="pypi.org",
        llm_provider="anthropic",
        mcp_resolved={"ext": {"transport": "http", "url": "https://mcp.example.com/mcp"}},
        resolver=_fake_resolver,
    )
    assert d.deny_by_default is True
    # LLM host + admin domain + external MCP host all resolved and pinned.
    assert {"api.anthropic.com", "pypi.org", "mcp.example.com"} <= set(d.domains)
    assert {"1.2.3.4/32", "1.2.3.5/32", "9.9.9.9/32", "8.8.4.4/32"} <= _cidrs(d)


def test_egress_in_cluster_mcp_not_pinned_as_cidr():
    # In-cluster MCP is reached via same-namespace egress, not an ipBlock.
    d = resolve_egress(
        enabled=True, mode="required_allowlist", admin_allowlist="",
        llm_provider="anthropic",
        mcp_resolved={"demo": {"transport": "http", "url": "http://mcp-tools.atlas.svc.cluster.local/mcp"}},
        resolver=_fake_resolver,
    )
    assert "mcp-tools.atlas.svc.cluster.local" not in d.domains


def test_egress_wildcard_is_unresolved():
    d = resolve_egress(
        enabled=True, mode="required_allowlist", admin_allowlist="*.mycorp.internal",
        llm_provider="anthropic", mcp_resolved={}, resolver=_fake_resolver,
    )
    assert "*.mycorp.internal" in d.unresolved
    assert all("mycorp" not in c for c in d.cidrs)


def test_egress_user_choice_caps_to_admin_superset():
    # user requests two domains; only the one in user_allowlist_max is honored.
    d = resolve_egress(
        enabled=True, mode="user_choice", admin_allowlist="",
        user_allowlist_max="pypi.org", user_requested=["pypi.org", "evil.com"],
        llm_provider="anthropic", mcp_resolved={}, resolver=_fake_resolver,
    )
    assert "pypi.org" in d.domains
    assert "evil.com" not in d.domains


def test_egress_summary_shape():
    s = egress_summary(enabled=True, mode="required_allowlist", admin_allowlist="a.com,b.com", user_allowlist_max="")
    assert s == {
        "enabled": True,
        "mode": "required_allowlist",
        "deny_by_default": True,
        "admin_allowlist": ["a.com", "b.com"],
        "user_editable": False,
        "user_allowlist_max": [],
    }


# ---- deny-by-default NetworkPolicy ----

def test_netpol_deny_by_default_pins_only_allowlist():
    netpol = build_network_policy(
        run_id="11111111-2222-3333-4444-555555555555",
        namespace="atlas",
        llm_provider="anthropic",
        mcp_resolved={},
        deny_by_default=True,
        allow_cidrs=["1.2.3.4/32", "9.9.9.9/32"],
    )
    egress = netpol["spec"]["egress"]
    # No open 0.0.0.0/0 rule in deny-by-default mode.
    cidrs = [t["ipBlock"]["cidr"] for r in egress for t in r.get("to", []) if "ipBlock" in t]
    assert "0.0.0.0/0" not in cidrs
    assert {"1.2.3.4/32", "9.9.9.9/32"} <= set(cidrs)
    # DNS rule still present.
    assert any(
        any(p.get("port") == 53 for p in r.get("ports", []))
        for r in egress
    )


def test_netpol_deny_by_default_empty_allowlist_is_dns_only():
    netpol = build_network_policy(
        run_id="11111111-2222-3333-4444-555555555555",
        namespace="atlas",
        llm_provider="anthropic",
        mcp_resolved={},
        deny_by_default=True,
        allow_cidrs=[],
    )
    egress = netpol["spec"]["egress"]
    # Only the DNS rule -- nothing else is reachable.
    assert len(egress) == 1


def test_netpol_open_mode_unchanged():
    netpol = build_network_policy(
        run_id="11111111-2222-3333-4444-555555555555",
        namespace="atlas",
        llm_provider="anthropic",
        mcp_resolved={},
    )
    cidrs = [t["ipBlock"]["cidr"] for r in netpol["spec"]["egress"] for t in r.get("to", []) if "ipBlock" in t]
    assert "0.0.0.0/0" in cidrs
