FROM python:3.12-slim

RUN groupadd -g 1000 runner && \
    useradd -u 1000 -g runner -m runner && \
    pip install --no-cache-dir prefect

# Create writable tmp dir for code execution (root filesystem is read-only)
RUN mkdir -p /tmp/workdir && chown runner:runner /tmp/workdir

COPY python_runner_flow.py /app/python_runner_flow.py
COPY agent_flow.py /app/agent_flow.py

WORKDIR /tmp/workdir
USER runner

ENTRYPOINT ["python", "/app/python_runner_flow.py"]
