# RHEL UBI 9-based Dockerfile for MCP HTTP Mock Server
# This Dockerfile creates a containerized MCP server for testing and demonstration

FROM registry.access.redhat.com/ubi9/ubi:latest

# Set working directory
WORKDIR /app

# Create non-root user
RUN groupadd -r mcpuser && useradd -r -g mcpuser mcpuser

# Install system dependencies
RUN dnf update -y && \
    dnf install -y --allowerasing \
        python3.11 \
        python3.11-pip \
        curl \
        hostname \
    && dnf clean all

# Copy application files
COPY main.py .
COPY run.sh .
COPY mcp-config.json .
COPY README.md .

# Create requirements.txt for this specific service
RUN echo "fastmcp>=2.10.6" > requirements.txt

# Create virtual environment and install dependencies as root first
RUN python3.11 -m venv /app/venv && \
    /app/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# Change ownership to non-root user
RUN chown -R mcpuser:mcpuser /app

# Switch to non-root user
USER mcpuser

# Set up Python environment
ENV VIRTUAL_ENV=/app/venv
ENV PATH="/app/venv/bin:$PATH"

# Expose the MCP server port
EXPOSE 8005

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    MCP_MOCK_TOKEN_1=test-api-key-123 \
    MCP_MOCK_TOKEN_2=another-test-key-456

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8005/mcp || exit 1

# Start the MCP server
CMD ["python3", "main.py"]
