# Use Fedora as base image
FROM fedora:latest

# Set working directory
WORKDIR /app

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install system dependencies including Python and Node.js
RUN dnf update -y && dnf install -y     python3     python3-pip     python3-virtualenv     nodejs     npm     curl     hostname     sudo     && dnf clean all

# Install uv for better Python dependency management
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mkdir -p /root/.local/bin
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
ARG VITE_APP_NAME="Chat UI"
ENV VITE_APP_NAME=${VITE_APP_NAME}
# build and delete the node_modules
RUN  npm run build && rm -rf node_modules

# Switch back to app directory and copy backend code
WORKDIR /app
COPY backend/ ./backend/
# Copy new config directory (defaults & overrides if present)
COPY config/ ./config/

# Copy other necessary files
COPY docs/ ./docs/
COPY scripts/ ./scripts/
COPY test/ ./test/

# Create required runtime & config directories (before ownership change)
RUN mkdir -p \
        /app/backend/logs \
        /app/config/defaults \
        /app/config/overrides \
        /app/runtime/logs \
        /app/runtime/feedback \
        /app/runtime/uploads && \
    # Seed overrides from defaults if overrides is empty
    if [ -d /app/config/defaults ] && [ "$(ls -A /app/config/overrides 2>/dev/null | wc -l)" = "0" ]; then \
        cp -n /app/config/defaults/* /app/config/overrides/ 2>/dev/null || true; \
    fi && \
    # Place keep files so directories exist even if empty at runtime
    touch /app/runtime/logs/.gitkeep /app/runtime/feedback/.gitkeep /app/runtime/uploads/.gitkeep

# Configure sudo for appuser (needed for Playwright browser installation)
RUN echo "appuser ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers

# Set up uv for appuser
RUN mkdir -p /home/appuser/.local/bin && \
    if [ -f "/root/.local/bin/uv" ]; then cp /root/.local/bin/uv /home/appuser/.local/bin/; fi && \
    mkdir -p /home/appuser/.cache && \
    chown -R appuser:appuser /home/appuser/.local /home/appuser/.cache

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Set up Python environment as appuser
ENV PATH="/home/appuser/.local/bin:$PATH"
RUN /home/appuser/.local/bin/uv python install 3.12
RUN /home/appuser/.local/bin/uv venv venv --python 3.12
ENV VIRTUAL_ENV=/app/venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python dependencies using uv
RUN /home/appuser/.local/bin/uv pip install -r requirements.txt

# Expose port
EXPOSE 8000

# Set environment variables
ENV PYTHONPATH=/app \
    NODE_ENV=production \
    APP_CONFIG_DEFAULTS=/app/config/defaults \
    APP_CONFIG_OVERRIDES=/app/config/overrides \
    RUNTIME_LOG_DIR=/app/runtime/logs \
    RUNTIME_FEEDBACK_DIR=/app/runtime/feedback

# Start the application
CMD ["python3", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]