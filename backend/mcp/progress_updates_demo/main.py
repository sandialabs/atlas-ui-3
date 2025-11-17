#!/usr/bin/env python3
"""
Progress Updates Demo MCP Server using FastMCP.

This server demonstrates the enhanced progress reporting capabilities
that allow MCP servers to send viewable updates to the frontend during
tool execution, including:
- Canvas updates (plots, HTML, images)
- Rich system messages
- Progress artifacts

To use these features from an MCP server, send special formatted messages
via ctx.report_progress() with the message field containing:
  "MCP_UPDATE:{json_payload}"

Supported update types:
- canvas_update: Display HTML/images in canvas during execution
- system_message: Add rich messages to chat history
- artifacts: Send file artifacts during execution
"""

from __future__ import annotations

import asyncio
import json
import base64
from typing import Any, Dict

from fastmcp import FastMCP, Context


# Initialize the MCP server
mcp = FastMCP("Progress Updates Demo")


def create_progress_html(step: int, total: int, message: str) -> str:
    """Create an HTML progress visualization."""
    percentage = int((step / total) * 100)
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: Arial, sans-serif;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
            }}
            .progress-container {{
                background: rgba(255, 255, 255, 0.2);
                border-radius: 10px;
                padding: 20px;
                backdrop-filter: blur(10px);
            }}
            .progress-bar {{
                width: 100%;
                height: 30px;
                background: rgba(255, 255, 255, 0.3);
                border-radius: 15px;
                overflow: hidden;
                margin: 10px 0;
            }}
            .progress-fill {{
                height: 100%;
                background: linear-gradient(90deg, #4CAF50, #8BC34A);
                width: {percentage}%;
                transition: width 0.3s ease;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
            }}
            h1 {{
                margin: 0 0 10px 0;
            }}
            .step-info {{
                font-size: 18px;
                margin: 10px 0;
            }}
        </style>
    </head>
    <body>
        <div class="progress-container">
            <h1>Task Progress</h1>
            <div class="step-info">Step {step} of {total}</div>
            <div class="progress-bar">
                <div class="progress-fill">{percentage}%</div>
            </div>
            <p>{message}</p>
        </div>
    </body>
    </html>
    """


def create_chart_html(data: Dict[str, int]) -> str:
    """Create a simple bar chart HTML."""
    max_value = max(data.values()) if data else 1
    bars = ""
    for label, value in data.items():
        percentage = int((value / max_value) * 100)
        bars += f"""
        <div class="bar-container">
            <div class="bar-label">{label}</div>
            <div class="bar-wrapper">
                <div class="bar-fill" style="width: {percentage}%">
                    <span class="bar-value">{value}</span>
                </div>
            </div>
        </div>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                font-family: Arial, sans-serif;
                padding: 20px;
                background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
            }}
            .chart-container {{
                background: white;
                border-radius: 10px;
                padding: 20px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
            }}
            h2 {{
                color: #333;
                margin-top: 0;
            }}
            .bar-container {{
                margin: 15px 0;
            }}
            .bar-label {{
                font-weight: bold;
                margin-bottom: 5px;
                color: #555;
            }}
            .bar-wrapper {{
                background: #e0e0e0;
                border-radius: 5px;
                overflow: hidden;
                height: 30px;
            }}
            .bar-fill {{
                background: linear-gradient(90deg, #667eea, #764ba2);
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: flex-end;
                padding-right: 10px;
                transition: width 0.5s ease;
            }}
            .bar-value {{
                color: white;
                font-weight: bold;
            }}
        </style>
    </head>
    <body>
        <div class="chart-container">
            <h2>Processing Results</h2>
            {bars}
        </div>
    </body>
    </html>
    """


@mcp.tool
async def task_with_canvas_updates(
    task_name: str = "demo",
    steps: int = 5,
    interval_seconds: int = 2,
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """
    Execute a long-running task with visual progress updates in the canvas.
    
    This tool demonstrates how MCP servers can send canvas updates during
    execution, allowing users to see real-time visual progress indicators.
    
    Args:
        task_name: Name of the task to execute
        steps: Number of steps to process (default: 5)
        interval_seconds: Delay between steps (default: 2)
        ctx: MCP context for progress reporting
    
    Returns:
        Task completion summary with final results
    """
    total = max(1, int(steps))
    interval = max(1, int(interval_seconds))
    
    # Initial progress
    if ctx:
        await ctx.report_progress(
            progress=0,
            total=total,
            message=f"Starting {task_name}..."
        )
    
    # Process each step and send visual updates as artifacts
    for step in range(1, total + 1):
        await asyncio.sleep(interval)
        
        # Create progress visualization HTML
        html_content = create_progress_html(step, total, f"Processing {task_name}: Step {step}")

        # Send progress HTML as an artifact so it uses the HTML viewer
        if ctx:
            artifact_html = base64.b64encode(html_content.encode("utf-8")).decode("utf-8")
            update_payload = {
                "type": "artifacts",
                "artifacts": [
                    {
                        "name": f"progress_step_{step}.html",
                        "b64": artifact_html,
                        "mime": "text/html",
                        "size": len(html_content),
                        "description": f"Progress for {task_name} step {step}/{total}",
                        "viewer": "html",
                    }
                ],
                "display": {
                    "open_canvas": True,
                    "primary_file": f"progress_step_{step}.html",
                    "mode": "replace",
                },
                "progress_message": f"{task_name}: Step {step}/{total}",
            }
            await ctx.report_progress(
                progress=step,
                total=total,
                message=f"MCP_UPDATE:{json.dumps(update_payload)}",
            )
    
    # Final result with chart
    result_data = {
        "Items Processed": total * 10,
        "Errors Found": 2,
        "Warnings": 5,
        "Success Rate": 95
    }
    
    chart_html = create_chart_html(result_data)
    
    return {
        "results": {
            "task": task_name,
            "status": "completed",
            "steps_completed": total,
            "summary": result_data
        },
        "artifacts": [
            {
                "name": "final_results.html",
                "b64": base64.b64encode(chart_html.encode('utf-8')).decode('utf-8'),
                "mime": "text/html",
                "size": len(chart_html),
                "description": "Final processing results chart",
                "viewer": "html"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "final_results.html",
            "mode": "replace",
            "viewer_hint": "html"
        }
    }


@mcp.tool
async def task_with_system_messages(
    task_name: str = "analysis",
    stages: int = 4,
    interval_seconds: int = 2,
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """
    Execute a task with rich system messages displayed in chat history.
    
    This tool demonstrates how MCP servers can send rich system messages
    that appear as new items in the chat history during tool execution.
    
    Args:
        task_name: Name of the analysis task
        stages: Number of stages to process (default: 4)
        interval_seconds: Delay between stages (default: 2)
        ctx: MCP context for progress reporting
    
    Returns:
        Analysis completion summary
    """
    stage_names = [
        "Data Collection",
        "Data Validation",
        "Analysis",
        "Report Generation"
    ][:stages]
    
    total = len(stage_names)
    
    # Initial progress
    if ctx:
        await ctx.report_progress(
            progress=0,
            total=total,
            message=f"Starting {task_name}..."
        )
    
    # Process each stage and send system messages
    for i, stage in enumerate(stage_names, 1):
        await asyncio.sleep(interval_seconds)
        
        # Send system message
        if ctx:
            update_payload = {
                "type": "system_message",
                "message": f"**{stage}** - Stage {i}/{total} completed successfully",
                "subtype": "success",
                "progress_message": f"{task_name}: {stage}"
            }
            await ctx.report_progress(
                progress=i,
                total=total,
                message=f"MCP_UPDATE:{json.dumps(update_payload)}"
            )
    
    return {
        "results": {
            "task": task_name,
            "status": "completed",
            "stages_completed": total,
            "completion_message": f"All {total} stages completed successfully"
        }
    }


@mcp.tool
async def task_with_artifacts(
    task_name: str = "data_processing",
    files_to_generate: int = 3,
    interval_seconds: int = 2,
    ctx: Context | None = None,
) -> Dict[str, Any]:
    """
    Execute a task that generates and displays artifacts progressively.
    
    This tool demonstrates how MCP servers can send file artifacts during
    execution, allowing users to see intermediate results as they're generated.
    
    Args:
        task_name: Name of the processing task
        files_to_generate: Number of intermediate files to create (default: 3)
        interval_seconds: Delay between file generation (default: 2)
        ctx: MCP context for progress reporting
    
    Returns:
        Processing completion summary
    """
    total = max(1, int(files_to_generate))
    interval = max(1, int(interval_seconds))
    
    # Initial progress
    if ctx:
        await ctx.report_progress(
            progress=0,
            total=total,
            message=f"Starting {task_name}..."
        )
    
    # Generate intermediate files
    for file_num in range(1, total + 1):
        await asyncio.sleep(interval)
        
        # Create intermediate result HTML
        intermediate_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    padding: 20px;
                    background: linear-gradient(135deg, #89f7fe 0%, #66a6ff 100%);
                }}
                .result {{
                    background: white;
                    border-radius: 10px;
                    padding: 20px;
                    box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                }}
                h2 {{ color: #333; margin-top: 0; }}
                .data {{ color: #666; font-size: 18px; }}
            </style>
        </head>
        <body>
            <div class="result">
                <h2>Intermediate Result {file_num}</h2>
                <p class="data">Generated at step {file_num} of {total}</p>
                <p class="data">Processing status: In Progress</p>
            </div>
        </body>
        </html>
        """
        
        # Send artifact via structured progress message
        if ctx:
            artifact_data = {
                "type": "artifacts",
                "artifacts": [
                    {
                        "name": f"intermediate_result_{file_num}.html",
                        "b64": base64.b64encode(intermediate_html.encode('utf-8')).decode('utf-8'),
                        "mime": "text/html",
                        "size": len(intermediate_html),
                        "description": f"Intermediate result {file_num}",
                        "viewer": "html"
                    }
                ],
                "display": {
                    "open_canvas": True,
                    "primary_file": f"intermediate_result_{file_num}.html",
                    "mode": "replace"
                },
                "progress_message": f"Generated file {file_num}/{total}"
            }
            await ctx.report_progress(
                progress=file_num,
                total=total,
                message=f"MCP_UPDATE:{json.dumps(artifact_data)}"
            )
    
    # Final result
    final_html = """
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {
                font-family: Arial, sans-serif;
                padding: 20px;
                background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
            }
            .final-result {
                background: white;
                border-radius: 10px;
                padding: 30px;
                box-shadow: 0 4px 6px rgba(0,0,0,0.1);
                text-align: center;
            }
            h1 { color: #4CAF50; }
            .success-icon { font-size: 64px; }
        </style>
    </head>
    <body>
        <div class="final-result">
            <div class="success-icon">✓</div>
            <h1>Processing Complete!</h1>
            <p>All files have been generated successfully.</p>
        </div>
    </body>
    </html>
    """
    
    return {
        "results": {
            "task": task_name,
            "status": "completed",
            "files_generated": total
        },
        "artifacts": [
            {
                "name": "final_result.html",
                "b64": base64.b64encode(final_html.encode('utf-8')).decode('utf-8'),
                "mime": "text/html",
                "size": len(final_html),
                "description": "Final processing result",
                "viewer": "html"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "final_result.html",
            "mode": "replace",
            "viewer_hint": "html"
        }
    }


if __name__ == "__main__":
    mcp.run()
