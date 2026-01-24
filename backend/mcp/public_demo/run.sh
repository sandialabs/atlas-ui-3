#!/bin/bash
# Public Demo MCP Server - Run Script
#
# Usage:
#   ./run.sh              # Run as stdio (default)
#   ./run.sh --http       # Run as HTTP server on port 8002
#   ./run.sh --sse        # Run as SSE server on port 8002
#   ./run.sh --http 9000  # Run as HTTP server on custom port
#
# Updated: 2025-01-23

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if it exists
if [ -f "../../../.venv/bin/activate" ]; then
    source "../../../.venv/bin/activate"
elif [ -f "../../.venv/bin/activate" ]; then
    source "../../.venv/bin/activate"
fi

# Parse arguments
MODE="stdio"
PORT=8002

while [[ $# -gt 0 ]]; do
    case $1 in
        --http)
            MODE="http"
            shift
            # Check if next arg is a port number
            if [[ $1 =~ ^[0-9]+$ ]]; then
                PORT=$1
                shift
            fi
            ;;
        --sse)
            MODE="sse"
            shift
            # Check if next arg is a port number
            if [[ $1 =~ ^[0-9]+$ ]]; then
                PORT=$1
                shift
            fi
            ;;
        --port)
            shift
            PORT=$1
            shift
            ;;
        -h|--help)
            echo "Public Demo MCP Server"
            echo ""
            echo "Usage:"
            echo "  ./run.sh              Run as stdio (default)"
            echo "  ./run.sh --http       Run as HTTP server on port 8002"
            echo "  ./run.sh --sse        Run as SSE server on port 8002"
            echo "  ./run.sh --http 9000  Run as HTTP server on custom port"
            echo "  ./run.sh --port 9000  Specify port (use with --http or --sse)"
            echo ""
            echo "For testing with the main app, use --http and configure mcp.json:"
            echo '  "public_demo": {'
            echo '    "url": "http://localhost:8002/mcp",'
            echo '    "transport": "http"'
            echo '  }'
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Run the server
case $MODE in
    http)
        echo "Starting Public Demo MCP server (HTTP) on port $PORT..."
        python main.py --http --port "$PORT"
        ;;
    sse)
        echo "Starting Public Demo MCP server (SSE) on port $PORT..."
        python main.py --sse --port "$PORT"
        ;;
    stdio)
        echo "Starting Public Demo MCP server (stdio)..."
        python main.py
        ;;
esac
