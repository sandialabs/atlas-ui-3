# Hardened Container Image

Last updated: 2026-11-10

`Dockerfile.hardened` builds a distroless variant of the Atlas image. It sits
beside the canonical `Dockerfile`, which stays the published and supported
default. Use the hardened image when you want the smallest runtime surface and
can live with the limitations below.

## What it is

- A `FROM scratch` runtime assembled from Chainguard Wolfi packages
  (`wolfi-baselayout`, CA bundle, `libstdc++`, `tzdata`, `python-3.14`).
- Runs as UID/GID `10001:10001` (user `atlas`), the same as the canonical image, so volumes and PVCs carry over between the two images.
- Same `EXPOSE 8000`, exec-form `HEALTHCHECK` and `CMD ["python", "main.py"]`
  as the canonical image, same environment variables and paths
  (`/app/config`, `/app/logs`, `/app/runtime`, `/data`).
- Alembic files are included, so the Kubernetes init container
  (`python -m atlas.modules.chat_history.schema_migration`) works unchanged.
- The frontend build and the `uv sync --frozen` dependency install are copies
  of the canonical logic. Keep the two files in sync when either changes.

## What is absent

| Absent | Consequence |
| --- | --- |
| Shell (`sh`, `bash`, busybox) | `docker exec ... sh` fails. Debug with `python -c`, `docker logs`, or an ephemeral debug container. Config-driven hooks and agent-portal commands that are shell scripts or use `#!/usr/bin/env` cannot run. |
| Package manager (`apk`), `pip`, `ensurepip`, `uv`, compilers | Nothing can be installed at runtime. |
| Node.js / npm | The frontend is built in a separate stage and only the static bundle is shipped. |
| Stdio MCP servers (by default) | `atlas/mcp/` is not in the image, so no bundled stdio server can start. Entries in `config/mcp.json` that use `command: python mcp/<name>/main.py` will fail discovery with a logged error. Use HTTP/SSE MCP servers, or opt in at build time. |
| `test/`, `docs/`, `scripts/`, `atlas/tests` | Same as the canonical image. |

## Opting in to stdio MCP servers

Pass the catalog names (directories under `atlas/mcp/`) as a space-separated
build argument:

```bash
docker build --build-arg ATLAS_STDIO_MCP_SERVERS="calculator filesystem" \
  -f Dockerfile.hardened -t atlas-hardened .
```

Only the listed directories are copied, plus `atlas/mcp/common/` when a listed
server imports it. The build fails on an unknown name. Python extras are
installed only for servers that need them: `pptx_generator` adds the `pptx`
extra; `basictable`, `code-executor`, `csv_reporter`, `duckduckgo` and
`pdfbasic` add `mcp-demos` (pandas, matplotlib, scipy, ... which enlarges the
image considerably). All other servers need only the core dependencies. Keep
`config/mcp.json` consistent with what you built in.

## Verifying

```bash
# Build (use --format docker with Podman so HEALTHCHECK is recorded)
podman build --format docker -f Dockerfile.hardened -t atlas-hardened:local .
# Runtime surface, read-only code, healthcheck, proxy-secret enforcement
CONTAINER_ENGINE=podman ATLAS_TEST_IMAGE=atlas-hardened:local \
  bash test/pr-validation/test_pr1200_hardened_image.sh
# With opt-in servers, tell the script what to expect
ATLAS_EXPECT_STDIO_MCP="calculator" ATLAS_TEST_IMAGE=atlas-hardened:calc \
  bash test/pr-validation/test_pr1200_hardened_image.sh
# Vulnerabilities
trivy image --severity CRITICAL,HIGH --ignore-unfixed atlas-hardened:local
```

CI runs the same steps in the `hardened-image` job. Its Trivy scans (OS and
Python libraries) are report-only: the output is visible in the job log but
never fails it.

## Base images, Python version and digests

- Builder and runtime both install the Wolfi package `python-${ATLAS_PYTHON_MINOR}`
  (default `3.14`), and the build fails if either interpreter reports another
  minor, so a venv cannot be built for one minor and run on another. An
  exec-form check in the final stage also compares the venv's `pyvenv.cfg`
  with the running interpreter and imports native extensions.
- The `cgr.dev/chainguard/python:latest` image is not used: `latest` is the only
  free tag and tracks the newest Python (3.15 at the time of writing), for which
  many locked dependencies (pydantic-core, duckdb, numpy, psycopg2-binary, ...)
  publish no wheels. Versioned tags (for example `python:3.14`) require a
  Chainguard subscription. Wolfi still ships `python-3.14`, so this recipe
  assembles it with apk.
- To switch later (a subscription with a versioned tag, or `uv.lock` gaining
  3.15 wheels): replace the builder with `python:<minor>-dev` and the final
  rootfs/`scratch` stage with `python:<minor>`, pin both by digest, set
  `ATLAS_PYTHON_MINOR` to that minor, reset `ENTRYPOINT []` (the image's
  entrypoint is the interpreter) and move the `10001` user and the `--chown`
  directories over to the new base. Keep the exec-form version checks.
- The `wolfi-base` image is pinned by digest. To bump: pull
  `cgr.dev/chainguard/wolfi-base:latest`, copy the index digest from
  `podman image inspect --format '{{.RepoDigests}}'` into both `FROM` lines,
  rebuild, run the validation script and the Trivy scan.
- When `uv.lock` gains wheels for a newer Python, raise `ATLAS_PYTHON_MINOR`
  in the same change (and in `python-X.Y` availability checks).

## Limitations

- The Wolfi apk repository is rolling: the base digest pins the tooling, but
  `apk add` fetches current package versions at build time. Rebuilds pick up
  fixes (good for CVEs) but are not bit-for-bit reproducible.
- Not published yet. Build it yourself from the repository until a `:hardened`
  tag exists.
- Hooks and agent-portal commands that need a shell are unsupported.
