# Code Executor v2 — RHEL 9 / UBI base.
#
# This image is intended to be deployed as its own pod (separate from the
# main Atlas pod). It bakes the curated data-science / engineering
# package set so that user code can `import` them without needing
# network access at runtime.
#
# The image runs as a non-root user with no capabilities; the actual
# sandbox enforcement (Landlock + user/net namespace + rlimits) is
# applied by `_sandbox_launch_v2.py` per `python` tool call.
#
# Required at the host / OpenShift cluster level:
#   * Linux kernel >= 5.13 with CONFIG_SECURITY_LANDLOCK
#   * Unprivileged user namespaces enabled (kernel.unprivileged_userns_clone=1)
#
# Build:
#   podman build -t atlas-code-executor-v2:dev -f Dockerfile .
#
# Run (local dev):
#   podman run --rm -p 8011:8011 atlas-code-executor-v2:dev

FROM registry.access.redhat.com/ubi9/python-311:latest

# ---------------------------------------------------------------------------
# System packages: git for the optional git_clone tool, plus libGL / libgomp
# for opencv / numpy. Everything else comes from pip.
# ---------------------------------------------------------------------------
USER 0
RUN dnf install -y --setopt=install_weak_deps=False \
        git \
        mesa-libGL \
        libgomp \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# ---------------------------------------------------------------------------
# Pre-create the workspaces dir with permissive perms so the unprivileged
# runtime user can mkdir per-session subdirs.
# ---------------------------------------------------------------------------
RUN mkdir -p /workspaces && chmod 1777 /workspaces

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
WORKDIR /app
COPY --chown=1001:0 . /app

USER 1001
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    HOME=/tmp \
    CODE_EXECUTOR_V2_WORKSPACES_DIR=/workspaces \
    MCP_CODE_EXECUTOR_V2_HOST=0.0.0.0 \
    MCP_CODE_EXECUTOR_V2_PORT=8011

RUN pip install --upgrade pip \
 && pip install -e .

EXPOSE 8011

# Use exec form so the python process is PID 1 and receives signals.
CMD ["python", "main.py"]
