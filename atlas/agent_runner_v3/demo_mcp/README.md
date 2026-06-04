# Demo HTTP MCP server (Agent Portal V3 testing)

A tiny, stdlib-only Streamable-HTTP MCP server used to exercise the Agent
Portal V3 end-to-end: it proves that an agent launched as a Kubernetes Job can
actually reach an MCP server and call its tools from inside the sealed pod.

## Tools

- `get_project_secret_code` — returns a fixed, unguessable token
  (`SKY-PENGUIN-42`). If an agent's final answer contains this token, the tool
  definitely ran inside the pod (the model can't know it otherwise). This is
  the E2E proof.
- `multiply(a, b)` — returns `a * b`.

## Why in-cluster

Each agent run gets a NetworkPolicy that blocks RFC1918 / link-local egress
(allowing only DNS + public 80/443). An in-cluster Service resolves to a
ClusterIP in the cluster's private range, so the policy would block it — except
`build_network_policy()` now detects an in-cluster MCP host (`*.svc`,
`*.svc.cluster.local`, or a bare service name) and adds a same-namespace egress
allowance. That's why the demo is deployed as a Service rather than run on the
host.

## Deploy

```bash
# 1. Make sure the agent-runner image is in the cluster
./atlas/agent_runner_v3/build_and_load.sh

# 2. Deploy the demo MCP server (default namespace: atlas)
./atlas/agent_runner_v3/demo_mcp/deploy.sh
```

The Deployment runs `server.py` (mounted via ConfigMap) on the agent-runner
image, so there's no second image to build. Keeping this pod running also pins
the agent image against kubelet image-GC on disk-pressured dev nodes.

## Use

`mcp.json` already contains an `mcp_tools_demo` entry pointing at
`http://mcp-tools.atlas.svc.cluster.local/mcp`. In the Agent Portal V3 UI:

1. Select **mcp_tools_demo** under MCP servers.
2. Prompt, e.g.: *"Use the available tool to look up the secret project code
   and tell me what it is."*
3. Launch. The pod logs should show a `tool_call` → `tool_result` for
   `mcp_tools_demo__get_project_secret_code` and a final answer containing
   `SKY-PENGUIN-42`.

## Demonstrating the NetworkPolicy (egress self-check)

Tick **Run network egress self-check** on the launch form (or POST
`{"egress_check": true}` to `/api/agent-portal-v3/runs`) to have the agent
probe a mix of destinations from inside the pod before it starts work and log
the result as `egress` lines:

```
egress  ALLOWED https://www.google.com -> HTTP 200 in 0.23s
egress  BLOCKED http://169.254.169.254/ -> connection error: All connection attempts failed
egress  BLOCKED http://10.0.0.53/ -> connection error: All connection attempts failed
```

Public HTTP(S) is permitted; the cloud-metadata link-local address
(`169.254.169.254`) and RFC1918 private ranges are blocked by the per-run
NetworkPolicy. Note that `www.google.com:443` is *allowed* by design — the
policy blocks private/link-local ranges and non-80/443 ports, not the public
internet. To probe your own targets instead of the defaults, set
`ATLAS_EGRESS_CHECK` to a comma-separated URL list on the agent container.
