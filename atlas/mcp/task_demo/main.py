#!/usr/bin/env python3
"""
Task Demo MCP Server using FastMCP 3.x

Demonstrates the adaptive task polling feature (ToolTask API) where
long-running tools can optionally run as background tasks with the
client polling for completion.

FastMCP 3.x features demonstrated:
- @mcp.tool(task=True) — optional background task mode
- @mcp.tool(task=TaskConfig(mode="required")) — required task mode
- ctx.report_progress() — progress updates during task execution
- ctx.is_background_task — detect if running as background task
- ctx.task_id — access the background task ID
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict

from fastmcp import Context, FastMCP
from fastmcp.server.tasks.config import TaskConfig

mcp = FastMCP("Task Demo")


@mcp.tool(task=True)
async def data_pipeline(
    records: int = 100,
    batch_size: int = 25,
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """Simulate a data processing pipeline with optional background task mode.

    When the client supports tasks, this tool runs in the background and
    the client polls for completion. Otherwise it runs inline.

    The `task=True` decorator enables optional task mode — the server
    advertises task support and the client decides whether to use it.

    Args:
        records: Total number of records to process (default: 100)
        batch_size: Records per batch (default: 25)
        ctx: FastMCP context for progress reporting

    Returns:
        Pipeline execution summary with batch details
    """
    start = time.perf_counter()
    records = max(1, min(records, 500))
    batch_size = max(1, min(batch_size, records))
    total_batches = (records + batch_size - 1) // batch_size

    is_bg = ctx.is_background_task if ctx else False
    task_id = ctx.task_id if ctx else None

    if ctx:
        await ctx.info(
            f"Pipeline started: {records} records, {total_batches} batches"
            + (f" (background task {task_id})" if is_bg else " (inline)")
        )

    processed = 0
    batch_results = []

    for batch_num in range(1, total_batches + 1):
        batch_count = min(batch_size, records - processed)

        # Simulate batch processing
        await asyncio.sleep(0.5)
        processed += batch_count

        batch_results.append({
            "batch": batch_num,
            "records_processed": batch_count,
            "cumulative": processed,
        })

        if ctx:
            await ctx.report_progress(
                progress=processed,
                total=records,
                message=f"Batch {batch_num}/{total_batches}: processed {processed}/{records} records",
            )

    elapsed = round((time.perf_counter() - start) * 1000, 3)

    return {
        "results": {
            "operation": "data_pipeline",
            "status": "completed",
            "total_records": records,
            "batches": total_batches,
            "execution_mode": "background_task" if is_bg else "inline",
            "task_id": task_id,
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": elapsed,
            "batch_details": batch_results,
        },
    }


@mcp.tool(task=TaskConfig(mode="required"))
async def report_generation(
    report_type: str = "summary",
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """Generate a report that always runs as a background task.

    Uses `task=TaskConfig(mode="required")` to force background task
    execution. The client must poll for completion.

    This demonstrates the "required" task mode where the tool refuses
    to run inline and always uses the task infrastructure.

    Args:
        report_type: Type of report — "summary", "detailed", or "full"
        ctx: FastMCP context for progress reporting

    Returns:
        Generated report data
    """
    start = time.perf_counter()

    stages = {
        "summary": ["Gathering data", "Computing metrics", "Formatting"],
        "detailed": ["Gathering data", "Computing metrics", "Analyzing trends", "Formatting"],
        "full": ["Gathering data", "Computing metrics", "Analyzing trends", "Cross-referencing", "Formatting", "Validating"],
    }

    steps = stages.get(report_type, stages["summary"])
    total = len(steps)

    task_id = ctx.task_id if ctx else None

    if ctx:
        await ctx.info(f"Report generation started (task {task_id}): {report_type} with {total} stages")

    for i, step in enumerate(steps):
        await asyncio.sleep(1)
        if ctx:
            await ctx.report_progress(
                progress=i + 1,
                total=total,
                message=f"Stage {i + 1}/{total}: {step}",
            )

    elapsed = round((time.perf_counter() - start) * 1000, 3)

    return {
        "data": {
            "report_type": report_type,
            "sections": [
                {"title": "Overview", "content": f"This {report_type} report covers key metrics."},
                {"title": "Metrics", "content": {"active_users": 1500, "revenue": "$45,200", "growth": "12%"}},
                {"title": "Recommendations", "content": ["Increase marketing spend", "Optimize onboarding flow"]},
            ],
            "generated_at": "2026-03-13T10:00:00Z",
            "task_id": task_id,
        },
        "results": {
            "operation": "report_generation",
            "status": "completed",
            "report_type": report_type,
            "stages_completed": total,
            "execution_mode": "background_task",
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": elapsed,
        },
    }


@mcp.tool(task=True)
async def health_check_sweep(
    targets: int = 5,
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """Run health checks across multiple services with task support.

    Demonstrates a practical use case for background tasks — checking
    multiple services where each check takes time.

    Args:
        targets: Number of services to check (default: 5)
        ctx: FastMCP context for progress reporting

    Returns:
        Health check results for all services
    """
    start = time.perf_counter()
    targets = max(1, min(targets, 20))

    services = [
        "api-gateway", "auth-service", "user-service", "billing-service",
        "notification-service", "search-service", "analytics-service",
        "cache-layer", "message-queue", "storage-service",
        "ml-inference", "cdn-proxy", "rate-limiter", "config-service",
        "audit-log", "scheduler", "webhook-relay", "metrics-collector",
        "feature-flags", "session-store",
    ][:targets]

    is_bg = ctx.is_background_task if ctx else False

    if ctx:
        await ctx.report_progress(
            progress=0, total=targets,
            message=f"Starting health checks for {targets} services",
        )

    results = []
    for i, service in enumerate(services):
        # Simulate varying check durations
        await asyncio.sleep(0.3)

        # Mock health status
        healthy = i % 7 != 0  # Every 7th service is degraded
        results.append({
            "service": service,
            "status": "healthy" if healthy else "degraded",
            "response_ms": 45 + (i * 12),
            "last_error": None if healthy else "Connection timeout (mock)",
        })

        if ctx:
            status = "healthy" if healthy else "DEGRADED"
            await ctx.report_progress(
                progress=i + 1,
                total=targets,
                message=f"Checked {service}: {status} ({i + 1}/{targets})",
            )

    healthy_count = sum(1 for r in results if r["status"] == "healthy")
    elapsed = round((time.perf_counter() - start) * 1000, 3)

    return {
        "data": {
            "services_checked": targets,
            "healthy": healthy_count,
            "degraded": targets - healthy_count,
            "overall_status": "healthy" if healthy_count == targets else "degraded",
            "checks": results,
        },
        "results": {
            "operation": "health_check_sweep",
            "status": "completed",
            "execution_mode": "background_task" if is_bg else "inline",
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": elapsed,
        },
    }


if __name__ == "__main__":
    mcp.run(show_banner=False)
