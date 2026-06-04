# Atlas Agent Runner V3 -- minimal headless container that runs one task.
# Built and shipped to k3s via:
#   podman build -t localhost/atlas-agent-runner:dev -f atlas/agent_runner_v3/Dockerfile .
#   podman save -o /tmp/atlas-agent-runner.tar localhost/atlas-agent-runner:dev
#   sudo k3s ctr images import /tmp/atlas-agent-runner.tar

FROM python:3.12-slim

# Non-root by default to satisfy the runAsNonRoot security context the
# Job manifest sets.
RUN groupadd --gid 10001 agent && useradd --uid 10001 --gid agent --create-home agent

WORKDIR /app

RUN pip install --no-cache-dir httpx==0.27.2

COPY atlas/agent_runner_v3/runner.py /app/runner.py
RUN chown -R agent:agent /app

USER 10001:10001

ENTRYPOINT ["python", "/app/runner.py"]
