#!/usr/bin/env python3
"""
MCP Server with 64 tools for testing UI with many tools.
Demonstrates that the collapsible UI can handle servers with large numbers of tools.
"""

from fastmcp import FastMCP

# Create the MCP server
mcp = FastMCP("ManyToolsDemo")

# Generate 64 tools dynamically to test UI scalability
# Categories: data, analytics, file, network, system, database, security, report

TOOL_CATEGORIES = [
    ("data", 10, "Process and transform data"),
    ("analytics", 10, "Analyze data and generate insights"),
    ("file", 8, "File operations and management"),
    ("network", 8, "Network operations and monitoring"),
    ("system", 8, "System administration tasks"),
    ("database", 8, "Database operations"),
    ("security", 6, "Security and encryption tasks"),
    ("report", 6, "Report generation and formatting"),
]

# Dynamically create tools for each category
for category, count, description_base in TOOL_CATEGORIES:
    for i in range(1, count + 1):
        tool_name = f"{category}_operation_{i}"
        tool_description = f"{description_base} - Operation {i}"

        # Use exec to create properly named functions
        exec(f"""
@mcp.tool()
def {tool_name}(input_data: str = "default") -> str:
    '''
    {tool_description}

    Args:
        input_data: Input data to process

    Returns:
        str: Result of the operation
    '''
    return f"Executed {tool_name} with input: {{input_data}}"
""")

if __name__ == "__main__":
    mcp.run(show_banner=False)

