"""Regression checks for the published runtime and development Compose boundary."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SECRETS = (
    "CAPABILITY_TOKEN_SECRET", "MCP_TOKEN_ENCRYPTION_KEY",
    "MINIO_ROOT_PASSWORD", "POSTGRES_PASSWORD", "PROXY_SECRET",
)


def test_published_runtime_is_multistage_nonroot():
    recipe = (ROOT / "Dockerfile").read_text()
    stages = re.split(r"^FROM ", recipe, flags=re.M)[1:]
    names = set()
    for stage in stages:
        image, *rest = stage.splitlines()[0].split()
        assert image in names or re.search(r"@sha256:[a-f0-9]{64}$", image)
        if rest:
            names.add(rest[-1])
    final = stages[-1]
    assert final.startswith("python-base AS runtime\n")
    assert "USER 10001:10001" in final
    assert "/api/heartbeat" in final
    assert 'os.environ.get(\'PORT\', \'8000\')' in final
    assert "HEALTHCHECK " in final
    assert "/usr/local/lib/python3.12/ensurepip" in final
    assert "/usr/local/lib/python3.12/site-packages/pip*" in final
    assert "uv sync --frozen --no-dev --extra mcp-demos --no-editable" in recipe
    assert "--ignore-requires-python" not in recipe
    assert "COPY --from=frontend-build /app/frontend/dist /app/atlas/static" in recipe
    for tree in ("docs", "test", "scripts", "mocks"):
        assert f"COPY {tree}/" not in recipe
    assert not re.search(r"\b(?:sudo|npm|nodejs|gcc|uv sync)\b", final)
    assert "COPY pyproject.toml uv.lock README.md ./" in recipe
    assert "COPY .env.example ./atlas/.env.example" in recipe


def test_compose_is_loopback_only_with_private_persistent_storage():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    for service in services.values():
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:")
        if "image" in service:
            assert re.search(r"@sha256:[a-f0-9]{64}$", service["image"])
    atlas = services["atlas-ui"]
    assert atlas["labels"]["org.atlas.deployment"] == "development-only"
    # Logs and data must be Docker-managed volumes: the runtime UID (10001)
    # cannot write host-owned bind mounts, so a bind mount here fails startup.
    assert "atlas-data:/data" in atlas["volumes"]
    assert "atlas-logs:/app/logs" in atlas["volumes"]
    assert set(compose["volumes"]) >= {"atlas-data", "atlas-logs"}
    env = dict(entry.split("=", 1) for entry in atlas["environment"])
    assert "DEBUG_MODE" not in env
    assert "AGENT_LOOP_STRATEGY" not in env
    assert env["CHAT_HISTORY_DB_URL"] == "duckdb:////data/chat_history.db"
    assert env["CAPABILITY_TOKEN_SECRET"].startswith("${CAPABILITY_TOKEN_SECRET:?")
    # DEBUG_MODE is gone, so proxy-secret enforcement is active; a missing
    # secret would 503 every protected route while the heartbeat still passes.
    assert env["PROXY_SECRET"].startswith("${PROXY_SECRET:?")
    # Token storage must live on the writable named volume, not the host-owned
    # ./config bind mount that the nonroot UID cannot write.
    assert env["MCP_TOKEN_STORAGE_DIR"] == "/data/tokens"
    assert env["S3_SECRET_KEY"].startswith("${MINIO_ROOT_PASSWORD:?")
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
    init = services["minio-init"]
    assert init["entrypoint"] == ["/bin/sh", "-ec"]
    script = init["command"][0]
    assert '"$$MINIO_ROOT_USER" "$$MINIO_ROOT_PASSWORD"' in script
    assert "mc anonymous set none myminio/atlas-files" in script
    assert "anonymous set download" not in script
    assert "${MINIO_ROOT_PASSWORD" not in script


@pytest.mark.parametrize("missing", REQUIRED_SECRETS)
@pytest.mark.parametrize("empty", [False, True])
def test_compose_rejects_missing_or_empty_required_secrets(missing, empty):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is not installed")
    env = {k: v for k, v in os.environ.items() if k not in REQUIRED_SECRETS}
    env.update({key: "container-regression-value-only" for key in REQUIRED_SECRETS})
    if empty:
        env[missing] = ""
    else:
        del env[missing]
    result = subprocess.run(
        ["docker", "compose", "--env-file", "/dev/null", "-f", str(ROOT / "docker-compose.yml"), "config"],
        env=env, text=True, capture_output=True, check=False,
    )
    if "compose" in result.stderr and "not a docker command" in result.stderr:
        pytest.skip("Docker Compose plugin is not installed")
    assert result.returncode != 0
    assert missing in result.stderr


def test_minio_credentials_are_not_interpreted_as_shell():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    script = compose["services"]["minio-init"]["command"][0].replace("$$", "$")
    credential = 'spaces " ; $(printf should-not-execute) `printf neither`'
    result = subprocess.run(
        ["/bin/sh", "-ec", "mc() { printf '<%s>' \"$@\"; printf '\\n'; }\n" + script],
        env={**os.environ, "MINIO_ROOT_USER": credential, "MINIO_ROOT_PASSWORD": credential},
        text=True, capture_output=True, check=True,
    )
    assert f"<{credential}><{credential}>" in result.stdout
    assert "<anonymous><set><none><myminio/atlas-files>" in result.stdout


def test_build_context_excludes_secrets_but_preserves_example():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    for pattern in (
        ".git", ".env", ".env.*", "**/.env", "**/.env.*", "**/node_modules",
        "**/.venv", "**/dist", "**/minio-data", "**/MagicMock*",
        "/config", "/data", "/logs", "/runtime",
    ):
        assert pattern in ignore
    assert ignore.index("!.env.example") > ignore.index("**/.env.*")
    assert "!.env.example" in (ROOT / ".gitignore").read_text().splitlines()
    assert ".dockerignore" not in (ROOT / ".gitignore").read_text().splitlines()
