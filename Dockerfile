# Keep the builder and runtime Python ABI identical.
FROM docker.io/library/python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS python-base

FROM docker.io/library/node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
ENV NPM_CONFIG_CACHE=/app/.npm
RUN npm ci --include=dev
COPY frontend/ ./
ARG VITE_APP_NAME="ATLAS"
ENV VITE_APP_NAME=${VITE_APP_NAME}
ARG VITE_FEATURE_POWERED_BY_ATLAS="false"
ENV VITE_FEATURE_POWERED_BY_ATLAS=${VITE_FEATURE_POWERED_BY_ATLAS}
ARG VITE_FEATURE_ANIMATED_LOGO="true"
ENV VITE_FEATURE_ANIMATED_LOGO=${VITE_FEATURE_ANIMATED_LOGO}
ARG VITE_FEATURE_RAG_CITATIONS="true"
ENV VITE_FEATURE_RAG_CITATIONS=${VITE_FEATURE_RAG_CITATIONS}
ARG GIT_HASH="unknown"
ARG APP_VERSION="unknown"
ENV GIT_HASH=${GIT_HASH} APP_VERSION=${APP_VERSION}
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.9.5@sha256:f459f6f73a8c4ef5d69f4e6fbbdb8af751d6fa40ec34b39a1ab469acd6e289b7 AS uv

FROM python-base AS python-build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY atlas/ ./atlas/
COPY .env.example ./atlas/.env.example
COPY prompts/ ./prompts/
COPY --from=frontend-build /app/frontend/dist /app/atlas/static
RUN find /app/atlas -type d \( -name tests -o -name __pycache__ \) -prune -exec rm -rf '{}' + && \
    find /app/atlas/mcp -type f -name 'test_*.py' -delete
# Use the lock and a real package, never a stub or an editable installation.
# The MCP extras are required by the bundled subprocess servers.
RUN uv sync --frozen --no-dev --extra mcp-demos --no-editable
RUN mkdir -p /app/config && \
    find /app/atlas/config -maxdepth 1 -type f \
      \( -name '*.json' -o -name '*.yml' -o -name '*.yaml' -o -name '*.md' \) \
      -exec cp '{}' /app/config/ \;

FROM python-base AS runtime
# The base includes pip for building Python applications; it is not a runtime
# requirement. Remove ensurepip too so it cannot recreate pip.
RUN rm -rf /usr/local/lib/python3.12/ensurepip \
      /usr/local/lib/python3.12/site-packages/pip* /usr/local/bin/pip* && \
    groupadd --gid 10001 atlas && \
    useradd --uid 10001 --gid atlas --create-home atlas && \
    mkdir -p /app/logs /app/runtime/logs /app/runtime/feedback /app/runtime/uploads /data && \
    chown -R atlas:atlas /app/logs /app/runtime /data
COPY --from=python-build /app/.venv /app/.venv
# Keep the source layout used by relative MCP cwd and prompt paths without
# depending on an editable package's build-time source directory.
COPY --from=python-build /app/atlas /app/atlas
COPY --from=python-build /app/prompts /app/prompts
COPY --from=python-build --chown=atlas:atlas /app/config /app/config
ARG GIT_HASH="unknown"
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    APP_CONFIG_DIR=/app/config \
    APP_LOG_DIR=/app/logs \
    RUNTIME_LOG_DIR=/app/runtime/logs \
    RUNTIME_FEEDBACK_DIR=/app/runtime/feedback \
    CHAT_HISTORY_DB_URL=duckdb:////data/chat_history.db \
    AGENT_PORTAL_DB_URL=duckdb:////data/agent_portal.db \
    GIT_HASH=${GIT_HASH} \
    GIT_COMMIT=${GIT_HASH} \
    ATLAS_HOST=0.0.0.0 \
    PORT=8000
USER 10001:10001
WORKDIR /app/atlas
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/api/heartbeat', timeout=4).close()"]
CMD ["python", "main.py"]
