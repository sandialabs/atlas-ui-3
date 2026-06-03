"""Tests for Agent Portal V3 Job + NetworkPolicy manifest builders.

Focus: the egress policy must allow an in-cluster MCP server (which resolves
to a ClusterIP in the cluster's private range and would otherwise be blocked by
the RFC1918 except-clause), while NOT loosening egress when every selected MCP
server is public.
"""

from __future__ import annotations

from atlas.modules.agent_portal_v3.job_template import (
    _has_in_cluster_mcp,
    _is_in_cluster_host,
    build_job_manifest,
    build_network_policy,
)


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
