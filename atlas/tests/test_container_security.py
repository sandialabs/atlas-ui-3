"""Regression checks for the published runtime and development Compose boundary."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

# Secrets the application stack always requires in Compose: Atlas cannot boot
# without them and no insecure fallback exists.
APP_SECRETS = ("CAPABILITY_TOKEN_SECRET", "MCP_TOKEN_ENCRYPTION_KEY", "PROXY_SECRET")
# The Compose stack runs against real object storage (USE_MOCK_S3=false) and
# has no IAM credential chain, so static S3 credentials are required.
S3_SETTINGS = ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY")
# Credential of the bundled PostgreSQL, required only when its Compose file is
# included. The bundled MinIO needs no separate credential: it reuses the
# application's S3_ACCESS_KEY / S3_SECRET_KEY pair (issue #1052).
POSTGRES_SECRETS = ("POSTGRES_PASSWORD",)

COMPOSE_COMBOS = {
    "application-only": ("docker-compose.yml",),
    "bundled-minio": ("docker-compose.yml", "docker-compose.minio.yml"),
    "bundled-postgres": ("docker-compose.yml", "docker-compose.postgres.yml"),
    "bundled-minio-and-postgres": (
        "docker-compose.yml", "docker-compose.minio.yml", "docker-compose.postgres.yml",
    ),
}

REQUIRED_BY_COMBO = [
    (name, files, secret)
    for name, files in COMPOSE_COMBOS.items()
    for secret in APP_SECRETS + S3_SETTINGS + (
        POSTGRES_SECRETS if "docker-compose.postgres.yml" in files else ()
    )
]

# Every candidate credential, pre-populated so a test only has to remove the
# one it exercises.
BASELINE_ENV = {
    **{name: "container-regression-value-only" for name in APP_SECRETS + S3_SETTINGS},
    "POSTGRES_PASSWORD": "container-regression-value-only",
}


def compose_path(name):
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present in this checkout")
    return path


def render_compose(files, env):
    """Run `docker compose config` for a backend combination.

    Configuration rendering needs no daemon, so this validates interpolation
    and merging exactly as a startup would see it.
    """
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is not installed")
    command = ["docker", "compose", "--env-file", "/dev/null"]
    for name in files:
        compose_path(name)
        command += ["-f", str(ROOT / name)]
    command.append("config")
    result = subprocess.run(
        command, env=env, text=True, capture_output=True, check=False,
    )
    if "compose" in result.stderr and "not a docker command" in result.stderr:
        pytest.skip("Docker Compose plugin is not installed")
    return result


def baseline_environment(drop=()):
    env = {key: value for key, value in os.environ.items() if key not in BASELINE_ENV}
    env.update({key: value for key, value in BASELINE_ENV.items() if key not in drop})
    return env


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


def test_base_compose_is_loopback_only_with_private_persistent_storage():
    compose = yaml.safe_load(compose_path("docker-compose.yml").read_text())
    services = compose["services"]
    # The base stack is the application only; bundled infrastructure is
    # opt-in (issue #1052).
    assert set(services) == {"atlas-ui"}
    atlas = services["atlas-ui"]
    for port in atlas.get("ports", []):
        assert port.startswith("127.0.0.1:")
    assert atlas["labels"]["org.atlas.deployment"] == "development-only"
    # Logs and data must be Docker-managed volumes: the runtime UID (10001)
    # cannot write host-owned bind mounts, so a bind mount here fails startup.
    assert "atlas-data:/data" in atlas["volumes"]
    assert "atlas-logs:/app/logs" in atlas["volumes"]
    assert set(compose["volumes"]) >= {"atlas-data", "atlas-logs"}
    env = dict(entry.split("=", 1) for entry in atlas["environment"])
    assert "DEBUG_MODE" not in env
    assert "AGENT_LOOP_STRATEGY" not in env
    # Chat history is hard-coded to the container-only absolute DuckDB path:
    # a host-oriented .env (duckdb:///data/chat_history.db, relative to the
    # project root) must not leak into the container, where only /data on the
    # atlas-data volume is writable by the nonroot runtime UID (issue #1052).
    assert env["CHAT_HISTORY_DB_URL"] == "duckdb:////data/chat_history.db"
    assert env["CAPABILITY_TOKEN_SECRET"].startswith("${CAPABILITY_TOKEN_SECRET:?")
    # DEBUG_MODE is gone, so proxy-secret enforcement is active; a missing
    # secret would 503 every protected route while the heartbeat still passes.
    assert env["PROXY_SECRET"].startswith("${PROXY_SECRET:?")
    # Token storage must live on the writable named volume, not the host-owned
    # ./config bind mount that the nonroot UID cannot write.
    assert env["MCP_TOKEN_STORAGE_DIR"] == "/data/tokens"
    # Object storage credentials belong to the application and are required:
    # this stack has no IAM credential chain, and they are never derived from
    # the optional bundled MinIO (issue #1052).
    for name in S3_SETTINGS:
        assert env[name].startswith("${" + name + ":?")
    # No bundled-service credential may leak into the base stack.
    assert "depends_on" not in atlas
    raw = compose_path("docker-compose.yml").read_text()
    assert "MINIO_ROOT" not in raw
    assert "${POSTGRES_PASSWORD" not in raw


def test_bundled_minio_file_is_opt_in_and_fail_closed():
    compose = yaml.safe_load(compose_path("docker-compose.minio.yml").read_text())
    services = compose["services"]
    assert set(services) == {"minio", "minio-init", "atlas-ui"}
    for name in ("minio", "minio-init"):
        service = services[name]
        assert re.search(r"@sha256:[a-f0-9]{64}$", service["image"])
        for port in service.get("ports", []):
            assert port.startswith("127.0.0.1:")
    environment = services["minio"]["environment"]
    # One credential pair for the whole stack: MinIO's root account is the
    # application's S3 pair, not an independently shipped default.
    assert environment["MINIO_ROOT_USER"].startswith("${S3_ACCESS_KEY:?")
    assert environment["MINIO_ROOT_PASSWORD"].startswith("${S3_SECRET_KEY:?")
    # The image runs as uid 1001 and declares its data volume at
    # /bitnami/minio/data: mounting there seeds the named volume with the
    # image's ownership, while an arbitrary mount path would be created
    # root-owned and the server would exit immediately.
    assert services["minio"]["volumes"] == ["minio-data:/bitnami/minio/data"]
    assert services["minio"]["command"] == 'server /bitnami/minio/data --console-address ":9001"'
    # The image ships curl but mc cannot run as the healthcheck: it needs a
    # configured alias and cannot write its configuration under the image's
    # HOME=/ as uid 1001. The readiness endpoint needs neither.
    assert services["minio"]["healthcheck"]["test"] == [
        "CMD", "curl", "-f", "http://127.0.0.1:9000/minio/health/ready",
    ]
    init = services["minio-init"]
    assert init["entrypoint"] == ["/bin/sh", "-ec"]
    # mc writes its alias configuration under HOME; the image runs as uid 1001
    # with a non-writable HOME=/, so the initializer redirects it to /tmp.
    assert init["environment"]["HOME"] == "/tmp"
    script = init["command"][0]
    assert '"$$MINIO_ROOT_USER" "$$MINIO_ROOT_PASSWORD"' in script
    assert "mc anonymous set none myminio/atlas-files" in script
    assert "anonymous set download" not in script
    assert "${" not in script
    # The application entry is an overlay fragment: it attaches the bundled
    # services as dependencies and pins the storage settings this stack
    # implies, so a host-oriented .env (e.g. S3_ENDPOINT=http://localhost:9000,
    # which would point the container at its own loopback) cannot leak in and
    # the bucket always matches what minio-init creates. The build stanza
    # keeps the file renderable on its own (agent_start.sh starts MinIO from
    # it alone); merging keeps the base build args.
    overlay = services["atlas-ui"]
    assert set(overlay) == {"build", "depends_on", "environment"}
    assert overlay["build"]["dockerfile"] == "Dockerfile"
    depends_on = overlay["depends_on"]
    assert depends_on["minio"]["condition"] == "service_healthy"
    assert depends_on["minio-init"]["condition"] == "service_completed_successfully"
    overlay_env = dict(entry.split("=", 1) for entry in overlay["environment"])
    assert overlay_env["S3_ENDPOINT"] == "http://minio:9000"
    assert overlay_env["S3_BUCKET_NAME"] == "atlas-files"
    assert overlay_env["S3_USE_SSL"] == "false"


def test_bundled_postgres_file_is_opt_in_and_fail_closed():
    compose = yaml.safe_load(compose_path("docker-compose.postgres.yml").read_text())
    services = compose["services"]
    assert set(services) == {"postgres", "atlas-ui"}
    postgres = services["postgres"]
    assert re.search(r"@sha256:[a-f0-9]{64}$", postgres["image"])
    for port in postgres.get("ports", []):
        assert port.startswith("127.0.0.1:")
    assert postgres["environment"]["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
    # When the bundled database is selected, chat history points at it with
    # the same single-sourced credential; the base file's container-only
    # DuckDB path is replaced by this overlay, never by a host .env value.
    # The build stanza keeps the file renderable on its own (agent_start.sh
    # starts PostgreSQL from it alone); merging keeps the base build args.
    overlay = services["atlas-ui"]
    assert set(overlay) == {"build", "environment"}
    assert overlay["build"]["dockerfile"] == "Dockerfile"
    overlay_env = dict(entry.split("=", 1) for entry in overlay["environment"])
    assert overlay_env["CHAT_HISTORY_DB_URL"].startswith(
        "postgresql://atlas:${POSTGRES_PASSWORD:?"
    )
    assert overlay_env["CHAT_HISTORY_DB_URL"].endswith("@postgres:5432/atlas_chat_history")


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("combo_name,files,secret", REQUIRED_BY_COMBO)
def test_compose_rejects_missing_or_empty_required_secrets(combo_name, files, secret, empty):
    if empty:
        env = baseline_environment()
        env[secret] = ""
    else:
        env = baseline_environment(drop=(secret,))
    result = render_compose(files, env)
    assert result.returncode != 0
    assert secret in result.stderr


@pytest.mark.parametrize("combo_name", list(COMPOSE_COMBOS))
def test_every_backend_combination_renders_successfully(combo_name):
    """All supported backend combinations produce a valid merged model.

    Guards against merge or schema errors that only appear when a
    combination is rendered with every credential it needs.
    """
    files = COMPOSE_COMBOS[combo_name]
    result = render_compose(files, baseline_environment())
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "combo_name,files,unused_secret",
    [
        ("application-only", ("docker-compose.yml",), "MINIO_ROOT_PASSWORD"),
        ("application-only", ("docker-compose.yml",), "POSTGRES_PASSWORD"),
        ("bundled-minio", ("docker-compose.yml", "docker-compose.minio.yml"), "POSTGRES_PASSWORD"),
        ("bundled-postgres", ("docker-compose.yml", "docker-compose.postgres.yml"), "MINIO_ROOT_PASSWORD"),
    ],
)
def test_unused_backend_credentials_are_ignored(combo_name, files, unused_secret):
    """A backend's credentials are required only when its Compose file is used.

    Encodes the issue #1052 acceptance criteria: DuckDB works without
    POSTGRES_PASSWORD and external S3 works without MINIO_ROOT_PASSWORD. The
    unused credential is deliberately SET in the environment to prove it is
    neither required nor leaked into the rendered stack.
    """
    sentinel = f"unused-{unused_secret.lower()}-sentinel"
    env = baseline_environment()
    env[unused_secret] = sentinel
    result = render_compose(files, env)
    assert result.returncode == 0, result.stderr
    assert sentinel not in result.stdout


def test_minio_credentials_are_not_interpreted_as_shell():
    compose = yaml.safe_load(compose_path("docker-compose.minio.yml").read_text())
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
