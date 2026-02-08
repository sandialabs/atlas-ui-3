#!/usr/bin/env python3
"""
Demo MCP server that demonstrates custom UI capabilities.

This server shows how MCP servers can return responses with custom_html
fields to modify the UI.
"""

import os
from typing import Any, Dict

from fastmcp import FastMCP

# Create the MCP server instance
mcp = FastMCP("UI Demo Server")

def load_template(template_name: str) -> str:
    """
    Load HTML template from templates directory.

    Args:
        template_name: Name of the template file to load

    Returns:
        The HTML content of the template

    Raises:
        FileNotFoundError: If the template file doesn't exist
    """
    template_path = os.path.join(os.path.dirname(__file__), "templates", template_name)
    with open(template_path, "r") as f:
        return f.read()

@mcp.tool
def create_button_demo() -> Dict[str, Any]:
    """
    Generate interactive HTML demonstrations showcasing advanced UI customization and dynamic interface capabilities.

    This UI prototyping tool creates sophisticated interactive demonstrations:

    **Interactive UI Components:**
    - Custom HTML button interfaces with advanced styling
    - Dynamic interaction patterns and user feedback systems
    - Professional design templates with modern aesthetics
    - Responsive layouts optimized for different screen sizes

    **UI Customization Features:**
    - Advanced CSS styling with modern design patterns
    - Interactive JavaScript functionality for user engagement
    - Professional color schemes and typography
    - Accessibility-compliant interface elements

    **Demonstration Capabilities:**
    - Real-time UI modification examples
    - Interactive component behavior showcases
    - Design pattern implementation demonstrations
    - User experience optimization examples

    **Technical Implementation:**
    - Clean HTML5 structure with semantic elements
    - Modern CSS3 styling with flexbox and grid layouts
    - Vanilla JavaScript for cross-browser compatibility
    - Base64 encoding for seamless artifact delivery

    **Use Cases:**
    - UI design prototyping and concept validation
    - Client demonstration and stakeholder presentations
    - Design system documentation and examples
    - Interactive tutorial and training materials
    - A/B testing interface variations
    - User experience research and testing

    **Professional Features:**
    - Production-ready code quality and structure
    - Cross-browser compatibility and standards compliance
    - Performance-optimized implementation
    - Maintainable and extensible code architecture

    **Integration Capabilities:**
    - Canvas viewer integration for immediate preview
    - Downloadable HTML for offline use and sharing
    - Framework-agnostic implementation
    - Easy customization and extension

    Returns:
        Dictionary containing:
        - results: Demo creation summary and success confirmation
        - artifacts: Interactive HTML demonstration as downloadable content
        - display: Optimized canvas viewer configuration for immediate preview
        - Interactive elements ready for user testing and evaluation
        Or error message if HTML generation or template loading fails
    """
    # Load the HTML template
    html_content = load_template("button_demo.html")

    # Convert to v2 MCP format with artifacts and display
    import base64
    html_base64 = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')

    return {
        "results": {
            "content": "Custom UI demo created successfully! Check the canvas panel for the interactive demo.",
            "success": True
        },
        "artifacts": [
            {
                "name": "ui_demo.html",
                "b64": html_base64,
                "mime": "text/html",
                "size": len(html_content.encode('utf-8')),
                "description": "Interactive UI demo with buttons and styling",
                "viewer": "html"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "ui_demo.html",
            "mode": "replace",
            "viewer_hint": "html"
        }
    }

@mcp.tool
def create_data_visualization() -> Dict[str, Any]:
    """
    Create a simple data visualization using HTML and CSS.

    Returns:
        Dictionary with custom HTML containing a bar chart visualization
    """
    # Load the HTML template
    html_content = load_template("data_visualization.html")

    # Convert to v2 MCP format with artifacts and display
    import base64
    import datetime
    html_base64 = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')

    return {
        "results": {
            "content": "Data visualization created and displayed in the canvas panel.",
            "data_points": {
                "sales": 75,
                "satisfaction": 92,
                "market_share": 58
            }
        },
        "artifacts": [
            {
                "name": "data_visualization.html",
                "b64": html_base64,
                "mime": "text/html",
                "size": len(html_content.encode('utf-8')),
                "description": "Sales and customer satisfaction data visualization",
                "viewer": "html"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "data_visualization.html",
            "mode": "replace",
            "viewer_hint": "html"
        },
        "meta_data": {
            "chart_type": "bar_chart",
            "data_points": 3,
            "metrics": ["sales", "satisfaction", "market_share"],
            "tool_execution": "create_data_visualization executed successfully",
            "timestamp": f"{datetime.datetime.now().isoformat()}"
        }
    }

@mcp.tool
def create_form_demo() -> Dict[str, Any]:
    """
    Create a demo form to show interactive UI capabilities.

    Returns:
        Dictionary with custom HTML containing an interactive form
    """
    # Load the HTML template
    html_content = load_template("form_demo.html")

    # Convert to v2 MCP format with artifacts and display
    import base64
    html_base64 = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')

    return {
        "results": {
            "content": "Interactive form demo created! You can interact with the form in the canvas panel.",
            "form_type": "demo"
        },
        "artifacts": [
            {
                "name": "interactive_form.html",
                "b64": html_base64,
                "mime": "text/html",
                "size": len(html_content.encode('utf-8')),
                "description": "Interactive form demo with input validation",
                "viewer": "html"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "interactive_form.html",
            "mode": "replace",
            "viewer_hint": "html"
        },
        "meta_data": {
            "form_fields": ["name", "email", "message"],
            "interactive": True,
            "validation": "client_side"
        }
    }

@mcp.tool
def get_image() -> Dict[str, Any]:
    """
    Return the badmesh.png image from the ui-demo directory.

    Returns:
        Dictionary with the image data encoded in base64
    """
    # Get the path to the image file
    image_path = os.path.join(os.path.dirname(__file__), "badmesh.png")

    # Read the image file as binary
    with open(image_path, "rb") as f:
        image_data = f.read()

    # Encode the image data in base64
    import base64
    image_base64 = base64.b64encode(image_data).decode('utf-8')

    # Return the image data with appropriate MIME type
    return {
        "results": {
            "content": "Image retrieved successfully!"
        },
        "artifacts": [
            {
                "name": "badmesh.png",
                "b64": image_base64,
                "mime": "image/png",
                "size": len(image_data),
                "description": "Bad mesh image for demonstration",
                "viewer": "image"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "badmesh.png",
            "mode": "replace",
            "viewer_hint": "image"
        }
    }

@mcp.tool
def create_iframe_demo() -> Dict[str, Any]:
    """
    Create a demo showing how to embed external content using iframes.

    This demonstrates the v2 MCP iframe capability for embedding interactive
    external content like dashboards, visualizations, or web applications.

    IMPORTANT - CSP Configuration Required:
        To display external URLs in iframes, the SECURITY_CSP_VALUE environment
        variable must include the iframe URL in the frame-src directive.

        Example for https://www.sandia.gov/:
        SECURITY_CSP_VALUE="... frame-src 'self' blob: data: https://www.sandia.gov/; ..."

        Without proper CSP configuration, the browser will block the iframe.

    Returns:
        Dictionary with iframe display configuration
    """
    return {
        "results": {
            "content": "Iframe demo created! An external webpage will be displayed in the canvas panel.",
            "iframe_url": "https://www.sandia.gov/"
        },
        "artifacts": [],
        "display": {
            "open_canvas": True,
            "type": "iframe",
            "url": "https://www.sandia.gov/",
            "title": "Example Website",
            "sandbox": "allow-scripts allow-same-origin",
            "mode": "replace"
        }
    }

@mcp.tool
def create_html_with_iframe() -> Dict[str, Any]:
    """
    Create an HTML artifact that includes an embedded iframe.

    This demonstrates how MCP tools can return HTML content with embedded
    iframes that will be properly rendered in the canvas panel.

    IMPORTANT - CSP Configuration Required:
        To display external URLs in iframes, the SECURITY_CSP_VALUE environment
        variable must include the iframe URL in the frame-src directive.

        Example for https://www.sandia.gov/:
        SECURITY_CSP_VALUE="... frame-src 'self' blob: data: https://www.sandia.gov/; ..."

        Without proper CSP configuration, the browser will block the iframe.

    Returns:
        Dictionary with HTML artifact containing an iframe
    """
    html_content = """<!DOCTYPE html>
<html>
<head>
    <style>
        body {
            margin: 0;
            padding: 20px;
            font-family: Arial, sans-serif;
            background: #1a1a1a;
            color: #fff;
        }
        .iframe-container {
            width: 100%;
            height: 600px;
            border: 2px solid #444;
            border-radius: 8px;
            overflow: hidden;
        }
        h1 {
            color: #4a9eff;
            margin-bottom: 10px;
        }
        p {
            color: #aaa;
            margin-bottom: 20px;
        }
    </style>
</head>
<body>
    <h1>Embedded Content Demo</h1>
    <p>This HTML artifact includes an embedded iframe showing external content:</p>
    <div class="iframe-container">
        <iframe
            src="https://www.sandia.gov/"
            width="100%"
            height="100%"
            sandbox="allow-scripts allow-same-origin"
            frameborder="0">
        </iframe>
    </div>
</body>
</html>"""

    import base64
    html_base64 = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')

    return {
        "results": {
            "content": "HTML with embedded iframe created! Check the canvas panel."
        },
        "artifacts": [
            {
                "name": "iframe_demo.html",
                "b64": html_base64,
                "mime": "text/html",
                "size": len(html_content.encode('utf-8')),
                "description": "HTML page with embedded iframe",
                "viewer": "html"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "iframe_demo.html",
            "mode": "replace",
            "viewer_hint": "html"
        }
    }

@mcp.tool
async def run_system_diagnostics() -> Dict[str, Any]:
    """
    Run a comprehensive system diagnostics scan that takes approximately
    10 seconds to complete. Returns detailed technical telemetry data.

    Returns:
        Dictionary with raw diagnostic telemetry and interpretation instructions
    """
    import asyncio
    import random
    import datetime

    await asyncio.sleep(10)

    ts = datetime.datetime.now().isoformat()
    jitter = random.uniform(0.1, 2.5)
    loss = random.uniform(0.0, 0.8)
    iops_read = random.randint(12000, 48000)
    iops_write = random.randint(8000, 32000)
    p99_lat = random.uniform(1.2, 18.5)
    gc_pause = random.randint(20, 350)
    heap_used = random.randint(512, 3800)
    heap_max = 4096
    threads_active = random.randint(24, 256)
    threads_blocked = random.randint(0, 12)
    tcp_retransmits = random.randint(0, 45)
    tls_handshake_ms = random.uniform(8.0, 120.0)
    dns_ms = random.uniform(1.0, 50.0)
    cpu_usr = random.uniform(10.0, 85.0)
    cpu_sys = random.uniform(2.0, 25.0)
    cpu_iowait = random.uniform(0.0, 30.0)
    swap_used_mb = random.randint(0, 2048)
    oom_kills = random.randint(0, 3)
    ctx_switches = random.randint(50000, 500000)
    page_faults = random.randint(100, 10000)

    raw_output = f"""=== SYSTEM DIAGNOSTICS REPORT ===
Timestamp: {ts}
Node: prod-app-7f8b2c.cluster-east.internal

--- NETWORK SUBSYSTEM ---
RTT (p50/p95/p99): 2.3ms / 8.7ms / {p99_lat:.1f}ms
Jitter: {jitter:.2f}ms | Packet loss: {loss:.3f}%
TCP retransmits: {tcp_retransmits} | RST recv: {random.randint(0, 5)}
Backlog depth: {random.randint(0, 128)} | SYN queue overflow: {random.randint(0, 3)}
TLS handshake (avg): {tls_handshake_ms:.1f}ms | DNS resolution (avg): {dns_ms:.1f}ms
Conn pool saturation: {random.uniform(20, 95):.1f}%
Ephemeral port exhaustion: {random.randint(0, 2)} events

--- STORAGE I/O ---
IOPS (r/w): {iops_read}/{iops_write}
Throughput: {random.randint(100, 800)}MB/s read | {random.randint(50, 400)}MB/s write
Avg latency (r/w): {random.uniform(0.2, 5.0):.2f}ms / {random.uniform(0.5, 8.0):.2f}ms
Queue depth: {random.randint(1, 64)} | Disk util: {random.uniform(10, 98):.1f}%
fsync stalls: {random.randint(0, 15)} | IO scheduler: mq-deadline

--- COMPUTE ---
CPU (usr/sys/iowait/idle): {cpu_usr:.1f}% / {cpu_sys:.1f}% / {cpu_iowait:.1f}% / {max(0, 100 - cpu_usr - cpu_sys - cpu_iowait):.1f}%
Load avg (1/5/15): {random.uniform(0.5, 12.0):.2f} / {random.uniform(0.5, 8.0):.2f} / {random.uniform(0.5, 6.0):.2f}
Context switches/s: {ctx_switches}
Involuntary preemptions: {random.randint(100, 5000)}

--- MEMORY / GC ---
Heap: {heap_used}MB / {heap_max}MB ({heap_used * 100 // heap_max}% utilized)
GC pause (max): {gc_pause}ms | GC cycles (last min): {random.randint(2, 30)}
Page faults (major/minor): {random.randint(0, 50)}/{page_faults}
Swap used: {swap_used_mb}MB | OOM kills (24h): {oom_kills}
RSS: {random.randint(1024, 6000)}MB | Slab: {random.randint(100, 800)}MB

--- THREADS / CONCURRENCY ---
Active: {threads_active} | Blocked: {threads_blocked} | Waiting: {random.randint(0, 30)}
Deadlock detected: {"YES" if random.random() < 0.05 else "No"}
Lock contention (avg): {random.uniform(0.01, 5.0):.2f}ms
Thread pool rejection rate: {random.uniform(0, 2.0):.3f}%

--- HEALTH VERDICTS ---
Network: {"DEGRADED" if p99_lat > 10 or tcp_retransmits > 20 else "OK"}
Storage: {"DEGRADED" if iops_read < 15000 else "OK"}
Compute: {"WARNING" if cpu_usr + cpu_sys > 80 else "OK"}
Memory: {"CRITICAL" if heap_used * 100 // heap_max > 90 or oom_kills > 0 else "WARNING" if gc_pause > 200 else "OK"}
Threads: {"WARNING" if threads_blocked > 5 else "OK"}
Overall: {"NEEDS ATTENTION" if p99_lat > 10 or gc_pause > 200 or oom_kills > 0 else "HEALTHY"}
=== END REPORT ==="""

    return {
        "results": {
            "content": raw_output,
            "instructions_to_llm": (
                "The above is raw system diagnostic telemetry. "
                "Translate this into a plain-English summary for a non-technical user. "
                "Do NOT use jargon like IOPS, p99, GC, TCP, RTT, heap, or thread pool. "
                "Instead, explain what each section means in everyday language: "
                "is the network fast or slow, is the storage keeping up, "
                "is the computer overworked, is memory running low, "
                "and are there any problems that need attention. "
                "Highlight anything flagged as DEGRADED, WARNING, or CRITICAL. "
                "Keep the summary concise -- a few short paragraphs at most."
            )
        }
    }


if __name__ == "__main__":
    mcp.run()
