# Use RHEL 9 UBI as base image
FROM registry.access.redhat.com/ubi9/ubi:latest

# Set working directory
WORKDIR /app

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install system dependencies including Python
RUN dnf update -y && dnf install -y --allowerasing \
    python3 \
    python3-pip \
    curl \
    hostname \
    sudo \
    && dnf clean all

# Install Node.js 20.x from RHEL module stream
RUN dnf module enable nodejs:20 -y && \
    dnf install -y nodejs && \
    dnf clean all

# Install uv for better Python dependency management
RUN pip3 install uv && \
    mkdir -p /root/.local/bin && \
    ln -sf /usr/local/bin/uv /root/.local/bin/uv && \
    ln -sf /usr/local/bin/uvx /root/.local/bin/uvx
ENV PATH="/root/.local/bin:$PATH"

# Copy requirements first
COPY requirements.txt .

# Copy and install frontend dependencies (for caching)
COPY frontend/package*.json ./frontend/
WORKDIR /app/frontend
ENV NPM_CONFIG_CACHE=/app/.npm
# Install all dependencies including devDependencies needed for build
RUN npm ci --include=dev

# Build frontend
COPY frontend/ .
# Set default app name for build (can be overridden via build arg)
ARG VITE_APP_NAME="ATLAS"
ENV VITE_APP_NAME=${VITE_APP_NAME}
# Set default whether to display powered by atlas logo on welcome screen (can be overridden via build arg)
ARG VITE_FEATURE_POWERED_BY_ATLAS="false"
ENV VITE_FEATURE_POWERED_BY_ATLAS=${VITE_FEATURE_POWERED_BY_ATLAS}
# build and delete the node_modules
RUN  npm run build && rm -rf node_modules

# Switch back to app directory and copy atlas package code
WORKDIR /app
COPY atlas/ ./atlas/

# Copy other necessary files
COPY docs/ ./docs/
COPY test/ ./test/
COPY prompts/ ./prompts/
COPY pyproject.toml ./

# Create required runtime & config directories and seed config from package defaults
RUN mkdir -p \
        /app/atlas/logs \
        /app/config \
        /app/runtime/logs \
        /app/runtime/feedback \
        /app/runtime/uploads && \
    cp -n /app/atlas/config/*.json /app/atlas/config/*.yml /app/config/ 2>/dev/null || true && \
    touch /app/runtime/logs/.gitkeep /app/runtime/feedback/.gitkeep /app/runtime/uploads/.gitkeep

# Configure sudo for appuser (needed for Playwright browser installation)
RUN echo "appuser ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

# Set up uv for appuser
RUN mkdir -p /home/appuser/.local/bin && \
    cp /usr/local/bin/uv /home/appuser/.local/bin/uv && \
    cp /usr/local/bin/uvx /home/appuser/.local/bin/uvx 2>/dev/null || true && \
    mkdir -p /home/appuser/.cache && \
    chown -R appuser:appuser /home/appuser/.local /home/appuser/.cache

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Set up Python environment as appuser
ENV PATH="/home/appuser/.local/bin:$PATH"
RUN /home/appuser/.local/bin/uv python install 3.12
RUN /home/appuser/.local/bin/uv venv .venv --python 3.12
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install Python dependencies using uv
RUN /home/appuser/.local/bin/uv pip install -r requirements.txt

# Expose port
EXPOSE 8000

# Set environment variables
ENV PYTHONPATH=/app \
    NODE_ENV=production \
    APP_CONFIG_DIR=/app/config \
    RUNTIME_LOG_DIR=/app/runtime/logs \
    RUNTIME_FEEDBACK_DIR=/app/runtime/feedback

# Start the application using the atlas-server CLI or direct Python
WORKDIR /app/atlas
# Use environment variables for host/port configuration
# Default to 0.0.0.0 for container environments, can be overridden
ENV ATLAS_HOST=0.0.0.0
ENV PORT=8000
CMD ["python3", "main.py"]
