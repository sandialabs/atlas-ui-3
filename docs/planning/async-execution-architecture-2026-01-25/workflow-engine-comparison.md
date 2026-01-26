# Workflow Engine Comparison: Temporal vs Prefect for AI-Planned Execution

**Created:** 2025-01-25

## Architecture Model

```
LLM (planner)
    |
    v
Human (approver)
    |
    v
Workflow Engine (executor) - Temporal or Prefect
    |
    v
MCP Tools (CAD/FEA/CFD/etc.)
```

AI plans the script, human approves, deterministic system executes.

## High-Level Comparison

| Dimension | Temporal | Prefect |
|-----------|----------|---------|
| Core philosophy | Deterministic state machine | Orchestrated Python execution |
| Determinism | Hard guarantee (enforced) | Best-effort (by discipline) |
| Replayability | Built-in, exact | Partial (logs + retries) |
| Failure recovery | Automatic, state-based | Retry-based |
| Auditability | Event-sourced history | Execution logs |
| AI-friendliness | High (with constraints) | Very high |
| Cognitive overhead | High | Low |
| Enterprise safety | Very high | Medium-high |
| Best fit | Mission-critical workflows | Data/automation pipelines |

## Temporal: Determinism as First-Class Constraint

### Strengths for AI-Planned Execution

1. **Determinism is enforced, not hoped for**
   - Workflows must be deterministic
   - Non-deterministic calls pushed into Activities
   - Bad plans fail loudly and early

2. **Replay = forensic superpower**
   - Replay executions exactly
   - See what AI planned vs what ran
   - Prove nothing "creative" happened at runtime
   - Critical for: regulated environments, post-incident analysis, "why did the AI do this?" reviews

3. **Human approval is a natural gate**
   - Approval modeled as: Signal, Manual activity, or External policy decision

4. **Strong blast-radius control**
   - Hard for AI to: loop infinitely, drift logic mid-execution, mutate behavior over time

### Tradeoffs

- Steep learning curve
- Must discipline AI output
- More ceremony up front

### Best For

- Safety-critical systems
- Infrastructure changes
- Compliance-heavy environments
- Long-running, stateful workflows
- "Never surprise me" execution philosophy

## Prefect: Controlled Python with Orchestration

### Strengths for AI-Planned Execution

1. **Extremely AI-friendly**
   - LLM can generate Python flows naturally
   - Lower friction for iteration

2. **Fast path to value**
   - Less boilerplate
   - Easier mental model
   - Faster onboarding

3. **Human approval is external but simple**
   - AI generates Python flow -> Human reviews diff -> CI runs Prefect flow

### Tradeoffs

- Determinism not enforced (Python can read time, use randomness, depend on external state)
- Replay != re-execution (logs, not guaranteed replays)
- Easier for AI to write problematic code (mutable globals, side-effects in flow logic)

### Best For

- Data pipelines
- ETL/ML workflows
- Internal automation
- Fast-moving teams
- "Humans are reviewing anyway" environments

## Key Distinction

**Temporal worldview:** "If it ran, it was valid." System enforces correctness.

**Prefect worldview:** "If it ran, we logged it." System observes correctness.

This distinction matters when AI is writing the plan.

## Engineering Automation Specifics (CAD/FEA/CFD/DOE)

### Workload Characteristics

- Expensive, long-running steps (minutes to days)
- External side effects (license servers, files, cluster jobs)
- Fan-out (Latin hypercube DOE) + fan-in (aggregate + decide)
- Iterative optimization loops
- Strict traceability requirements
- Approval gates needed

### Recommendation

**Temporal is better for engineering experiment orchestration:**
- Long-running, stateful loops are first-class
- Fan-out/fan-in is a core pattern (child workflows per design point)
- Determinism + event history gives forensic replay
- Retries/timeouts/heartbeats built for flaky external compute
- Signals make human approval and pause/resume natural

**Prefect works when:**
- Workflow logic isn't complex state machinery
- Mostly scheduling, retries, observability, launching compute
- Team is Python-first and okay with "determinism by discipline"

## Infrastructure Requirements

### Temporal

- Temporal Server (Helm chart: frontend, history, matching, worker services)
- PostgreSQL/MySQL/Cassandra (required persistence)
- Optional: Elasticsearch/OpenSearch for visibility queries
- Your Workers (separate deployments with your code)
- Web UI for ops visibility

### Prefect

- Prefect Server (API + UI + scheduler)
- PostgreSQL backend
- Workers (often K8s workers that launch Jobs)

Both deploy via Helm on Kubernetes. Prefect is simpler to stand up initially.

## Designing for Future Migration (Prefect -> Temporal)

### What Makes Switching Painful

1. **Workflow logic embedded in Prefect tasks** - Branching/state/optimization loops in Prefect flow code
2. **Implicit state** - "Current design" in Python variables, no durable artifact IDs
3. **Non-idempotent tasks** - Retry accidentally resubmits 200 CFD jobs

### What Makes Switching Easy

#### 1. Stable Execution API (Idempotent Tool Adapters)

```python
cad.modify(design_id, params) -> cad_rev_id
mesh.generate(cad_rev_id, settings) -> mesh_id
solve.submit(mesh_id, solver, settings) -> run_id
solve.wait(run_id) -> status
solve.fetch(run_id) -> result_id
post.extract(result_id, qoi_spec) -> metrics
```

Key points:
- Every call returns immutable IDs
- Every call accepts idempotency key (or derives one deterministically)
- Prefect calls these now; Temporal Activities call these later

#### 2. Artifact/Provenance as Truth

Store in object store/DB keyed by run IDs:
- Inputs (params/settings)
- Outputs (IDs)
- Logs
- Metadata (git SHA, solver version, license profile, cluster partition)

Orchestration becomes: "call step -> record IDs -> decide next"

#### 3. Plans in Small IR (Not Orchestrator Code)

LLM produces JSON "Experiment Plan":
- Variables + bounds
- DOE spec / optimizer spec
- Step pipeline template
- Concurrency caps
- Approval checkpoints

Prefect loads and runs the plan. Temporal can run the same plan later.

#### 4. Idempotent Retry Pattern

For side-effect actions, adapter should:
- If run_id exists, return it
- If job already submitted, don't submit again
- If artifact exists, reuse it

### Migration Reality

**Reusable (80-90%):**
- Tool adapters (CAD/FEA/CFD/Slurm)
- Artifact store + provenance schema
- Plan IR + validation + approval UI
- Postprocessing and metrics extraction

**Rewrite:**
- Orchestrator glue code (Prefect flow -> Temporal workflow+activities)
- Prefect-specific mapping/concurrency constructs

## Recommendation

**Start with Prefect but design for migration:**

1. Write flows that mostly: parse plan, iterate design points, call adapter functions, write artifact IDs
2. Avoid clever Prefect-first patterns as business logic
3. Keep engineering automation behind idempotent tool adapters
4. Treat orchestrator as thin scheduler over domain logic

This makes future migration mostly "swapping the engine under a well-designed transmission."

## Hybrid Pattern (Common in Enterprise)

```
AI
 |-> Prefect (exploration, iteration)
     |-> Human approval
         |-> Temporal (production execution)
```

- Prefect = AI sandbox
- Temporal = production executor
- Fast iteration + strong guarantees where it counts

## Summary

| If... | Then... |
|-------|---------|
| AI plans but not decides | Temporal is safer executor |
| AI assists but humans own correctness | Prefect is faster |

**Temporal prevents AI mistakes. Prefect helps humans recover from them.**
