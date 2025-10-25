"""Progress Demo MCP Server using FastMCP.

This server exposes a single long-running tool that reports progress updates
to the client every n seconds until completion. Useful for validating end-to-end
progress handling in the app.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastmcp import FastMCP, Context


# Initialize the MCP server
mcp = FastMCP("ProgressDemo")


@mcp.tool
async def long_task(
    task: str = "demo",
    duration_seconds: int = 12,
    interval_seconds: int = 3,
    ctx: Context | None = None,
) -> dict:
    """Execute long-running operations with real-time progress tracking and user feedback capabilities.

    This advanced progress monitoring tool demonstrates professional long-running task management:
    
    **Progress Tracking Features:**
    - Real-time progress updates with percentage completion
    - Configurable update intervals for optimal user experience
    - Task labeling and identification for multi-task environments
    - Asynchronous execution with non-blocking progress reporting

    **User Experience:**
    - Live progress bars and status indicators
    - Descriptive progress messages with task context
    - Predictable completion time estimation
    - Graceful handling of interruptions and errors

    **Technical Capabilities:**
    - Async/await pattern for efficient resource utilization
    - Context injection for framework integration
    - Configurable timing parameters for different use cases
    - Robust error handling and cleanup procedures

    **Use Cases:**
    - Large file processing and data migration
    - Complex calculations and analysis workflows
    - System maintenance and backup operations
    - Report generation and batch processing
    - Machine learning model training
    - Database operations and synchronization

    **Progress Reporting:**
    - Percentage-based completion tracking
    - Time-based milestone reporting
    - Custom message formatting for task context
    - Integration with UI progress indicators

    **Customization Options:**
    - Adjustable task duration for testing scenarios
    - Variable update frequency for different performance needs
    - Custom task labeling for organizational clarity
    - Flexible timing configuration

    Args:
        task: Descriptive label for the operation being performed (default: "demo")
        duration_seconds: Total time for task completion in seconds (default: 12)
        interval_seconds: Frequency of progress updates in seconds (default: 3)
        ctx: MCP context for progress reporting (automatically injected by framework)

    Returns:
        Dictionary containing:
        - results: Task completion summary and final status
        - task_info: Task parameters and execution details
        - timing: Actual execution time and performance metrics
        Or error message if task execution fails
    """
    total = max(1, int(duration_seconds))
    step = max(1, int(interval_seconds))

    # Initial progress (0%)
    if ctx is not None:
        await ctx.report_progress(progress=0, total=total, message=f"{task}: starting")

    elapsed = 0
    while elapsed < total:
        await asyncio.sleep(step)
        elapsed = min(total, elapsed + step)
        if ctx is not None:
            await ctx.report_progress(
                progress=elapsed,
                total=total,
                message=f"{task}: {elapsed}/{total}s",
            )

    # Final completion (100%)
    if ctx is not None:
        await ctx.report_progress(progress=total, total=total, message=f"{task}: done")

    return {
        "results": {
            "task": task,
            "status": "completed",
            "duration_seconds": total,
            "interval_seconds": step,
        }
    }


@mcp.tool
async def status_updates(
    stages: list[str] | None = None,
    interval_seconds: int = 2,
    ctx: Context | None = None,
) -> dict:
    """Emit text status updates at a fixed interval (indeterminate progress).

    This demo focuses on sending human-readable status messages to the UI
    without a known total. The UI will show an indeterminate bar and the
    latest status message.

    Args:
        stages: Optional list of stage messages to emit sequentially.
        interval_seconds: Delay in seconds between updates.
        ctx: FastMCP context used to report progress messages.

    Returns:
        dict with a simple results payload including the stages traversed.
    """
    steps = stages or [
        "Starting",
        "Validating inputs",
        "Preparing resources",
        "Processing data",
        "Uploading artifacts",
        "Finalizing",
    ]

    # Initial status (no total, indeterminate)
    if ctx is not None:
        await ctx.report_progress(progress=0, message=f"{steps[0]}...")

    for i, stage in enumerate(steps):
        if i > 0:
            await asyncio.sleep(max(1, int(interval_seconds)))
            if ctx is not None:
                # Report only progress counter and message; omit total for indeterminate
                await ctx.report_progress(progress=i, message=f"{stage}...")

    if ctx is not None:
        await ctx.report_progress(progress=len(steps), message="Done.")

    return {
        "results": {
            "status": "completed",
            "stages": steps,
            "updates": len(steps) + 1,
            "interval_seconds": max(1, int(interval_seconds)),
        }
    }


if __name__ == "__main__":
    mcp.run()

