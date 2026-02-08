## Plan: Decouple function-calling from the UI using a message broker

Status: proposal / planning

Last updated: 2025-11-08

This document describes a practical plan for introducing a message broker (RabbitMQ recommended as a first step) to decouple function/tool execution from the UI in the Atlas UI 3 backend. It focuses on minimal-change incremental integration with the current codebase (no immediate UI changes), concrete message contracts, PoC steps, and operational considerations.

## Executive summary

- Goal: move long-running or heavy tool/LLM executions out of the WebSocket request path and into independent worker processes while preserving streaming updates and approval UX.
- Recommendation: start with RabbitMQ (AMQP) for a PoC. It maps well to RPC-like request→worker→reply flows, supports reply_to/correlation_id, DLQs, and flexible routing.
- Approach: incremental — first brokerize tool execution after the existing approval step (minimal disruption). Later, we can evolve to broker-mediated approvals, replayable events (Kafka), or event-sourcing if needed.

## Why this fits the current codebase

- Current flow (important points)
  - WebSocket handler in `atlas/main.py` constructs a `ChatService` and accepts chat messages.
  - `ChatService.handle_chat_message` delegates to `ChatOrchestrator.execute`, which selects a mode (tools/agent/rag/plain).
  - `ToolsModeRunner.run` calls LLM and then `tool_utils.execute_tools_workflow` when tool calls are present.
  - `tool_utils.execute_single_tool` prepares args (injects signed file URLs and username), sends a `tool_approval_request` update to the UI, waits on an in-process `ToolApprovalManager`, and then calls `tool_manager.execute_tool(...)` directly. The same `update_callback` is used to stream progress to the UI.

- Because `prepare_tool_arguments` already tokenizes file URLs and injects required context, workers can receive all necessary inputs in the message payload and independently fetch files.

## Integration strategy (incremental path — recommended PoC)

1) Minimal-change PoC (Phase 1)
   - Keep approval behavior intact in `tool_utils.execute_single_tool`.
   - After approval, publish a `function_request` message to RabbitMQ instead of invoking `tool_manager.execute_tool` directly.
   - Keep emitting `tool_start` UI events immediately so the UI shows the job started.
   - Implement a separate worker service (small Python process) that consumes `function_request`, performs the tool call (via MCP or local tool manager), and publishes `tool_progress` and `tool_response` messages.
   - Add a backend bridge (consumer) that subscribes to the response exchange and forwards progress and final messages to the WebSocket via the existing `EventPublisher`/`update_callback` path. This keeps UI code unchanged.

2) Full decoupling (Phase 2 — optional later)
   - Move approval to be broker-aware (publish request immediately and gate execution by a distributed approval store or an approval topic). This requires reworking `approval_manager` to use a shared backing store (Redis or broker topics).
   - Consider Kafka if you need event replay, analytics, or very high throughput.

## Where to hook changes in the codebase

- Producer (backend): modify `atlas/application/chat/utilities/tool_utils.py` in `execute_single_tool` to publish to the broker after approval (Phase 1 change).
- Backend bridge: add a small consumer in backend infrastructure that subscribes to broker response topics and calls existing `notification_utils` / `EventPublisher` methods to forward messages to the UI. This can live under `atlas/infrastructure/` (for example `infrastructure/broker_bridge/`) or be a lightweight thread/task in the backend process.
- Worker: new microservice `tools_worker` (e.g., `services/tools_worker/`) that consumes `function_request` messages and invokes `tool_manager.execute_tool` or an MCP client. It publishes progress and final result messages.
- Optionally: add a shared datastore (Redis) for idempotency, cancellation flags, and short-lived session replay caches.

## Message contracts (recommended JSON shapes)

- function_request (published by backend after approval)

```json
{
  "correlation_id": "<tool_call.id>",
  "session_id": "<session_id>",
  "user_email": "user@example.com",
  "tool_name": "my_tool",
  "arguments": { /* filtered args after inject_context_into_args */ },
  "metadata": {
    "model": "gpt-4",
    "idempotency_key": "<tool_call.id>",
    "compliance_level": "internal",
    "allow_edit": false,
    "admin_required": false,
    "ttl_ms": 300000
  },
  "streaming": true,
  "reply_to": "results.backend-instance-1"
}
```

- tool_progress (published by worker while executing)

```json
{
  "correlation_id": "<tool_call.id>",
  "seq": 1,
  "chunk": "partial token text or structured update",
  "is_final_chunk": false,
  "timestamp": "2025-11-08T12:00:00Z"
}
```

- tool_response (final result)

```json
{
  "correlation_id": "<tool_call.id>",
  "status": "success",
  "result": { "content": "final content", "success": true },
  "error": null,
  "timestamp": "2025-11-08T12:00:10Z"
}
```

- cancel_request (publish from atlas/UI to cancel)
```json
{
  "correlation_id": "<tool_call.id>",
  "issued_by": "user@example.com",
  "timestamp": "..."
}
```

Notes:
- Use `correlation_id` to correlate progress and final messages to the original tool call. Use `seq` numbers to detect reordering.
- Pass filtered/parsed `arguments` including signed file URLs (already created by `inject_context_into_args`).
- Keep messages small; stream large results as chunks.

## Worker behavior (recommendations)

- Consume `function_request` messages from a durable queue.
- Implement idempotency: store `idempotency_key` results (Redis or DB) to avoid double execution on redelivery.
- Emit `tool_progress` messages frequently for streaming UX; include `seq` numbers.
- On transient errors, N retries with exponential backoff; on final failure, publish error `tool_response` and push request to a DLQ.
- Support cancellation: listen for `cancel_request` messages or periodically check a cancellation key in Redis.

## Ordering, scaling, and streaming guarantees

- Ordering: if you need strict ordering for progress chunks, either:
  - Use a single consumer per correlation_id (not always practical); or
  - Attach `seq` numbers and reorder or drop out-of-order messages in the backend bridge.
- Scaling: partition queues by tool_name or hash(session_id) for even distribution if throughput grows.

## Approval approaches (two options)

- Option A — Keep approvals in backend (recommended PoC)
  - Backend waits for UI approval as today, then publishes to the broker. Minimal code changes and UX remains identical.

- Option B — Broker-mediated approvals (advanced)
  - Publish request immediately; worker waits for approval messages or checks a distributed approval store. Requires reworking `approval_manager` to use Redis or broker topics and more operational complexity.

## Security & compliance

- Do not leak sensitive data: signed file URLs should be short-lived and scoped. Consider passing tokens instead of full contents when running workers in less-trusted environments.
- Use TLS and authentication for broker connections and enable ACLs so only permitted services can publish/consume exchange/queues.
- Include `compliance_level` metadata and enforce it in workers (this repo already has compliance-level logic; reuse it).

## Operational considerations

- Broker choice: RabbitMQ recommended for the RPC-like pattern. Consider Kafka if you need durable replay and high-throughput streaming across many services.
- Deployment: prefer managed RabbitMQ (CloudAMQP, AWS MQ) in production to reduce ops burden. For dev, local RabbitMQ or Docker-compose is fine.
- Monitoring: queue depth, message age, consumer health, DLQ rates, and message throughput.
- Backpressure: if queues grow, scale workers or implement admission control at the backend.

## Minimal PoC checklist (mapping to repo files)

Phase 1 (minimal-change PoC, low risk)

1. Add a lightweight RabbitMQ publisher and configuration.
   - Candidate location: `atlas/infrastructure/broker/producer.py` (or reuse existing infra patterns if present).

2. Modify `atlas/application/chat/utilities/tool_utils.py`:
   - After the approval step and `notification_utils.notify_tool_start`, publish `function_request` to broker instead of calling `tool_manager.execute_tool` inline.
   - Keep `notify_tool_start` so UI shows the job started.

3. Add a backend bridge (consumer) that subscribes to `results.*` and forwards `tool_progress` and `tool_response` messages into existing notification paths (`notification_utils.notify_tool_complete`, `notify_tool_error`, or `EventPublisher.publish_chat_response`).
   - Candidate location: `atlas/infrastructure/broker/bridge.py` or a small background task started from `infrastructure.app_factory` during lifespan startup.

4. Create `tools_worker` service (separate process):
   - Small Python process consuming `function_request` queue, calling `tool_manager.execute_tool` or invoking MCP clients, publishing progress and final responses.
   - Place as a new top-level directory `services/tools_worker/` or `atlas/tools_worker/` if you prefer to keep it inside the repo.

5. Tests:
   - Simulate a chat flow where a tool call is made, approval is granted, and worker returns progress + final. Confirm WebSocket receives progress and final messages.
   - Test worker crash/restart and message redelivery (durable queue).
   - Test cancel path and DLQ behavior.

Phase 2 (optional, later)

- Make approval distributed (use Redis + broker topics) and/or move to broker-first model.
- Add session replay and reconnect resilience (persist progress messages in a short-term cache to replay when users reconnect).

## Timeline (rough estimate)

- Decision + design: 1–2 days.
- PoC (Phase 1): 3–5 days (producer, worker, bridge, tests).
- Hardening & rollout: 1–3 weeks (HA, monitoring, ACLs, security review).

## Next steps (suggested)

1. Confirm approval strategy for PoC (Option A recommended).
2. Select broker (RabbitMQ recommended) and deployment mode (managed vs self-hosted).
3. I can draft the exact JSON schemas (full JSON Schema) and a small sequence diagram next.
4. If you want, I can implement the PoC changes (producer, bridge, and a simple worker) and run local tests in the dev container.

---

If you want the next artifact now, pick one:
- Full JSON Schema for the messages
- A small ASCII sequence diagram and exact exchange/queue names (recommended names included)
- A PoC implementation plan with exact code snippets to patch into `tool_utils.py` and a micro worker skeleton

I'll produce whichever you pick next.
