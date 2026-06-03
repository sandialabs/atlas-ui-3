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
