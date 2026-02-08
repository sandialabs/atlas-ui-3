# Async Execution Architecture: Plan-Approve-Execute Pattern

**Created:** 2026-01-25
**Status:** Proposal
**Author:** Architecture Discussion

## Overview

This document proposes an architecture for adding a planning phase to Atlas UI that enables:

1. **Planning Phase**: Generate structured plans from available tools, RAG sources, and user intent
2. **Validation**: Plans represented as Python-like code for linting and type-checking
3. **User Approval**: Human-in-the-loop before execution
4. **Async Execution**: Decoupled from Atlas UI, with observability and retry capabilities

## Problem Statement

Currently, Atlas UI executes tool calls synchronously within agent loops. For complex, long-running tasks, this approach has limitations:

- No structured planning visible to users before execution
- Limited observability into multi-step workflows
- No retry/resume capabilities for failed steps
- Execution tied to WebSocket connection lifetime

## Recommended Solution: Temporal.io

### Why Temporal Over Airflow/Dagster

| Criteria | Airflow | Dagster | Temporal |
|----------|---------|---------|----------|
| Dynamic workflows | Poor (static DAGs) | Good | Excellent |
| Human-in-the-loop | Hacky | Possible | First-class support |
| Real-time feedback | No | Limited | Yes (queries) |
| Code-first approach | YAML-ish | Decorators | Pure Python |
| Long-running tasks | Timeouts | Timeouts | Designed for it |
| Retry semantics | Basic | Good | Excellent |

**Why not Airflow/Dagster?**

- Both are designed for **scheduled batch pipelines**, not interactive workflows
- Airflow DAGs are defined at parse time - dynamic generation requires creating DAG files
- Neither handles "wait for user approval" elegantly

**Why Temporal?**

- Workflows are code, not config
- Native `workflow.wait_condition()` for user approval gates
- Excellent observability UI out of the box
- Automatic retries with exponential backoff
- Workflow state survives crashes and restarts

## Proposed Architecture

```
+-------------------------------------------------------------------+
|                        Atlas UI (existing)                         |
+-------------------------------------------------------------------+
|  User Chat -> Agent Loop -> Planning MCP -> Plan Preview           |
|                              |                                     |
|                     [User Approves Plan]                           |
|                              |                                     |
|                    Submit to Temporal                              |
|                              |                                     |
|              WebSocket <- Status Updates <- Temporal Worker        |
+-------------------------------------------------------------------+

+-------------------------------------------------------------------+
|                     Temporal Cluster                               |
+-------------------------------------------------------------------+
|  Workflow: ExecutePlanWorkflow                                     |
|    - Receives validated plan                                       |
|    - Executes steps as Activities                                  |
|    - Signals Atlas UI on progress                                  |
|    - Handles retries, timeouts, failures                           |
+-------------------------------------------------------------------+
|  Activities:                                                       |
|    - execute_mcp_tool(server, tool, args)                         |
|    - query_rag_source(source, query)                              |
|    - call_llm(messages)                                            |
|    - notify_user(message)                                          |
+-------------------------------------------------------------------+
```

## Component Design

### 1. Planning MCP Server

A dedicated MCP server that exposes planning capabilities:

**Input:**
- Available tools (serialized from existing MCP discovery via `mcp_tool_manager`)
- RAG sources (from `rag-sources.json`)
- User intent (natural language)
- Optional constraints (timeout, allowed tools, compliance level)

**Output:**
- Structured `ExecutionPlan` with steps, dependencies, expected outputs

```python
# Pseudo-interface for the planning tool
@tool
def create_execution_plan(
    user_intent: str,
    available_tools: list[ToolSpec],
    available_rag: list[RagSourceSpec],
    constraints: dict | None = None
) -> ExecutionPlan:
    """LLM-powered planning that returns a structured plan"""
    ...
```

**Integration with Atlas UI:**
- Add to `mcp.json` as a new MCP server
- Planning tool appears in tool list when enabled
- Agent loop can invoke planning as first step for complex tasks

### 2. Plan Representation (Python-like DSL)

Rather than arbitrary Python execution, use a **restricted DSL** that looks like Python but is validated and interpreted:

```python
# Plan output format - validated but not executed as raw Python
@plan(name="research_and_summarize", timeout="30m")
def execute():
    # Step 1: Gather information
    search_results = rag.search("confluence", query=user_query)

    # Step 2: Use tools (parallel where possible)
    with parallel():
        doc_a = tools.file_reader.read(path=search_results[0].path)
        doc_b = tools.file_reader.read(path=search_results[1].path)

    # Step 3: Synthesize
    summary = llm.complete(
        prompt=f"Summarize: {doc_a} and {doc_b}",
        model="claude-sonnet"
    )

    return {"summary": summary, "sources": search_results}
```

**Validation Approach:**

1. Parse with `ast.parse()` - validates Python syntax
2. Whitelist allowed function calls:
   - `rag.*` - RAG operations
   - `tools.*` - MCP tool calls
   - `llm.*` - LLM completions
   - `parallel()` - concurrency context manager
3. Type-check with stub files defining the DSL interface
4. Reject dangerous constructs:
   - Imports (except whitelisted)
   - `exec`, `eval`, `compile`
   - File system access outside DSL
   - Network access outside DSL

**Benefits:**
- Syntax errors caught before execution
- Type hints enable IDE support and validation
- Familiar Python syntax for developers
- Safe execution through interpretation, not `exec()`

### 3. Temporal Workflow Implementation

```python
from temporalio import workflow, activity
from temporalio.common import RetryPolicy
from dataclasses import dataclass

@dataclass
class ExecutionPlan:
    name: str
    steps: list[PlanStep]
    timeout_minutes: int = 30

@dataclass
class PlanStep:
    name: str
    action: str  # "rag.search", "tools.X.Y", "llm.complete"
    args: dict
    parallel: bool = False
    substeps: list["PlanStep"] | None = None

@activity.defn
async def execute_step(step: PlanStep) -> StepResult:
    """Execute a single plan step"""
    if step.action.startswith("rag."):
        return await execute_rag_action(step)
    elif step.action.startswith("tools."):
        return await execute_tool_action(step)
    elif step.action.startswith("llm."):
        return await execute_llm_action(step)
    else:
        raise ValueError(f"Unknown action: {step.action}")

@activity.defn
async def notify_progress(step_name: str, status: str) -> None:
    """Send progress update to Atlas UI via webhook/WebSocket"""
    ...

@workflow.defn
class ExecutePlanWorkflow:
    @workflow.run
    async def run(self, plan: ExecutionPlan) -> ExecutionResult:
        results = {}

        for step in plan.steps:
            if step.parallel and step.substeps:
                # Execute parallel steps concurrently
                tasks = [
                    workflow.execute_activity(
                        execute_step,
                        args=[s],
                        retry_policy=RetryPolicy(
                            maximum_attempts=3,
                            initial_interval=timedelta(seconds=1),
                            maximum_interval=timedelta(minutes=1),
                            backoff_coefficient=2.0,
                        ),
                        start_to_close_timeout=timedelta(minutes=5),
                    )
                    for s in step.substeps
                ]
                step_results = await asyncio.gather(*tasks)
                results[step.name] = step_results
            else:
                results[step.name] = await workflow.execute_activity(
                    execute_step,
                    args=[step],
                    retry_policy=RetryPolicy(maximum_attempts=3),
                    start_to_close_timeout=timedelta(minutes=5),
                )

            # Signal progress to Atlas UI
            await workflow.execute_activity(
                notify_progress,
                args=[step.name, "completed"],
                start_to_close_timeout=timedelta(seconds=30),
            )

        return ExecutionResult(
            plan_name=plan.name,
            results=results,
            status="completed"
        )
```

### 4. User Approval Flow

**Option A: Approval in Atlas UI (Recommended for Initial Implementation)**

```
1. User submits complex request
2. Agent invokes Planning MCP
3. Plan displayed in chat/canvas with step-by-step preview
4. User clicks "Approve" or "Modify"
5. On approval: Plan submitted to Temporal via REST API
6. Temporal workflow executes asynchronously
7. Progress updates streamed back via WebSocket
8. Final results displayed in canvas
```

**Option B: Approval as Temporal Signal (More Robust)**

```
1. User submits complex request
2. Plan submitted to Temporal immediately (workflow starts in "pending" state)
3. Workflow waits for approval signal: await workflow.wait_condition(lambda: self.approved)
4. User approves in Atlas UI -> sends signal to Temporal
5. Workflow continues execution
6. Benefits: Plan state persisted, survives Atlas UI restart
```

### 5. Atlas UI Integration Points

**Backend Changes:**

1. **New REST endpoint**: `POST /api/plans/submit` - Submit approved plan to Temporal
2. **New REST endpoint**: `GET /api/plans/{id}/status` - Poll plan execution status
3. **WebSocket message type**: `plan_progress` - Real-time execution updates
4. **New service**: `PlanExecutionService` - Coordinates with Temporal client

**Frontend Changes:**

1. **Plan Preview Component**: Renders plan as interactive tree/graph
2. **Approval UI**: Approve/Modify/Cancel buttons
3. **Execution Monitor**: Shows step-by-step progress with status indicators
4. **Results Display**: Final output rendered in canvas

**Configuration:**

```yaml
# New section in app settings
planning:
  enabled: true
  temporal_host: "localhost:7233"
  temporal_namespace: "atlas-plans"
  default_timeout_minutes: 30
  max_parallel_steps: 10
```

## Alternative Approaches

### Prefect (Lighter Weight)

If Temporal infrastructure seems heavy:

```python
from prefect import flow, task
from prefect.tasks import task_input_hash

@task(retries=2, cache_key_fn=task_input_hash)
async def execute_step(step: PlanStep) -> StepResult:
    ...

@flow(name="execute_plan", retries=2)
async def execute_plan(plan: ExecutionPlan) -> ExecutionResult:
    results = {}
    for step in plan.steps:
        results[step.name] = await execute_step(step)
    return ExecutionResult(results=results)
```

**Prefect Pros:**
- Simpler setup (can run without dedicated server initially)
- Good retry and caching
- Nice UI for observability

**Prefect Cons:**
- Less sophisticated human-in-the-loop support
- Fewer advanced workflow patterns

### Custom Solution with Celery + Redis

For minimal new infrastructure:

```python
from celery import Celery, chain, group

app = Celery('atlas_plans', broker='redis://localhost:6379')

@app.task(bind=True, max_retries=3)
def execute_step(self, step_data: dict) -> dict:
    ...

def execute_plan(plan: ExecutionPlan):
    # Build task chain/group from plan
    workflow = chain(*[execute_step.s(step) for step in plan.steps])
    return workflow.apply_async()
```

**Celery Pros:**
- Well-established, simple
- Minimal new infrastructure if Redis already in use

**Celery Cons:**
- Limited workflow orchestration
- No built-in observability UI
- Manual implementation of approval flow

## Implementation Phases

### Phase 1: Planning MCP (2-3 weeks)

1. Create Planning MCP server with `create_execution_plan` tool
2. Define `ExecutionPlan` schema and DSL grammar
3. Implement DSL parser and validator
4. Add plan preview UI component
5. Test with mocked execution

### Phase 2: Temporal Integration (2-3 weeks)

1. Set up Temporal cluster (docker-compose for dev)
2. Implement `ExecutePlanWorkflow` and activities
3. Create activity implementations for MCP tools, RAG, LLM
4. Add progress notification mechanism
5. Integrate with Atlas UI backend

### Phase 3: Frontend Integration (1-2 weeks)

1. Plan approval UI flow
2. Execution monitoring component
3. Results display in canvas
4. Error handling and retry UI

### Phase 4: Production Hardening (1-2 weeks)

1. Temporal cluster deployment configuration
2. Monitoring and alerting
3. Plan versioning and history
4. Compliance level enforcement in plans

## Questions to Resolve

Before implementation, clarify:

1. **Plan complexity**: Are plans mostly linear, or do we need complex branching/loops?
2. **Execution duration**: Expected range - minutes, hours, days?
3. **Failure handling**: Retry individual steps, or restart entire plan?
4. **User interaction during execution**: Can user modify/cancel mid-execution?
5. **Infrastructure constraints**: Can we run Temporal, or need something lighter?
6. **Multi-user considerations**: Plan isolation, resource limits, queuing?

## References

- [Temporal Documentation](https://docs.temporal.io/)
- [Prefect Documentation](https://docs.prefect.io/)
- [Dagster Documentation](https://docs.dagster.io/)
- [Python AST Module](https://docs.python.org/3/library/ast.html)

## Appendix: Infrastructure Requirements

### Temporal Cluster (Production)

```yaml
# docker-compose.yml addition
services:
  temporal:
    image: temporalio/auto-setup:1.22
    ports:
      - "7233:7233"
    environment:
      - DB=postgresql
      - DB_PORT=5432
      - POSTGRES_USER=temporal
      - POSTGRES_PWD=temporal
      - POSTGRES_SEEDS=postgres

  temporal-ui:
    image: temporalio/ui:2.21
    ports:
      - "8080:8080"
    environment:
      - TEMPORAL_ADDRESS=temporal:7233
```

### Temporal Worker

```python
# atlas/workers/plan_worker.py
import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

async def main():
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="atlas-plans",
        workflows=[ExecutePlanWorkflow],
        activities=[execute_step, notify_progress],
    )
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
```
