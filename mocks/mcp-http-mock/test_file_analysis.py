#!/usr/bin/env python3
"""
Test script to verify the analyze_file tool works correctly.

This script tests the file analysis functionality with different input types.
"""

import json
import base64
import tempfile
import os


def test_analyze_file_with_text():
    """Test analyzing a simple text file."""
    print("\n=== Test 1: Analyzing text content ===")
    
    # Simulate what the tool receives
    test_content = "Hello, this is a test file.\nIt has multiple lines.\nLine 3 here!"
    
    # Method 1: Base64 encoded (fallback method)
    content_b64 = base64.b64encode(test_content.encode('utf-8')).decode('utf-8')
    print(f"Base64 content (first 50 chars): {content_b64[:50]}...")
    
    # In actual use, the backend would provide a URL like:
    # https://atlas.example.com/api/files/download/key123?token=abc...
    print("In production: Backend provides URL like:")
    print("  https://atlas.example.com/api/files/download/key123?token=abc...")
    

def test_analyze_file_with_binary():
    """Test analyzing binary content."""
    print("\n=== Test 2: Analyzing binary content ===")
    
    # Simulate binary file
    binary_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
    content_b64 = base64.b64encode(binary_content).decode('utf-8')
    
    print(f"Binary content (first 20 bytes): {binary_content[:20]}")
    print("Expected: Tool detects as binary, shows '<Binary content>' message")


def test_file_url_rewriting():
    """Demonstrate how the backend rewrites filenames to URLs."""
    print("\n=== Test 3: Backend URL rewriting ===")
    
    scenarios = [
        {
            "name": "Without BACKEND_PUBLIC_URL (local/stdio servers)",
            "config": "BACKEND_PUBLIC_URL not set",
            "original": "report.pdf",
            "rewritten": "/api/files/download/key123?token=abc...",
            "works_for": "Local servers on same machine (localhost)"
        },
        {
            "name": "With BACKEND_PUBLIC_URL (remote HTTP/SSE servers)",
            "config": "BACKEND_PUBLIC_URL=https://atlas.example.com",
            "original": "report.pdf",
            "rewritten": "https://atlas.example.com/api/files/download/key123?token=abc...",
            "works_for": "Remote servers on different machines"
        }
    ]
    
    for scenario in scenarios:
        print(f"\nScenario: {scenario['name']}")
        print(f"  Config: {scenario['config']}")
        print(f"  Original filename: {scenario['original']}")
        print(f"  Rewritten to: {scenario['rewritten']}")
        print(f"  Works for: {scenario['works_for']}")


def print_example_usage():
    """Print example of how to use the analyze_file tool."""
    print("\n=== Example Usage ===")
    print("""
1. User attaches 'document.txt' in Atlas UI
2. LLM decides to analyze it: analyze_file(filename="document.txt")
3. Backend intercepts and rewrites:
   - If BACKEND_PUBLIC_URL set: analyze_file(filename="https://atlas.com/api/files/download/key?token=...")
   - If not set: analyze_file(filename="/api/files/download/key?token=...")
4. MCP tool receives the URL and downloads the file
5. Tool analyzes and returns:
   {
     "results": {
       "success": true,
       "filename": "document.txt",
       "file_size_bytes": 1234,
       "file_size_kb": 1.21,
       "content_type": "text/plain",
       "is_text_file": true,
       "text_preview": "First 500 characters...",
       "line_count": 42,
       "access_method": "URL download (remote MCP server compatible)",
       "note": "File was successfully downloaded from backend using tokenized URL"
     }
   }
""")


def print_requirements():
    """Print requirements for remote file access."""
    print("\n=== Requirements for Remote MCP Server File Access ===")
    print("""
For the analyze_file tool to work with remote MCP servers:

1. Atlas UI Backend Configuration:
   - Set BACKEND_PUBLIC_URL=https://your-atlas-domain.com
   - Restart backend to apply changes

2. Network Connectivity:
   - Remote MCP server must be able to reach the Atlas UI backend
   - HTTPS recommended for production
   - Firewall rules must allow traffic

3. MCP Server Configuration (optional):
   - Can set BACKEND_PUBLIC_URL environment variable in mcp.json
   - Example:
     {
       "mcp-http-mock": {
         "url": "http://remote-server:8005/mcp",
         "transport": "http",
         "env": {
           "BACKEND_PUBLIC_URL": "${BACKEND_PUBLIC_URL}"
         }
       }
     }

4. Security:
   - Files are protected by short-lived tokens (default 1 hour)
   - Tokens are user-specific
   - Use HTTPS in production
""")


if __name__ == "__main__":
    print("=" * 70)
    print("File Analysis Tool - Test and Demonstration")
    print("=" * 70)
    
    test_analyze_file_with_text()
    test_analyze_file_with_binary()
    test_file_url_rewriting()
    print_example_usage()
    print_requirements()
    
    print("\n" + "=" * 70)
    print("To test with actual Atlas UI:")
    print("1. Start this MCP server: python main.py")
    print("2. Configure in Atlas UI mcp.json")
    print("3. Set BACKEND_PUBLIC_URL in Atlas UI .env")
    print("4. Attach a file and ask the LLM to analyze it")
    print("=" * 70)
