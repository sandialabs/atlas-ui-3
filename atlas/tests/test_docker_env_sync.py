"""Test to ensure docker-compose.yml environment variables stay in sync with .env.example.

This test ensures that the environment variables set in docker-compose.yml for the atlas-ui
service match those defined in .env.example, with appropriate exceptions for Docker-specific
configurations.
"""

import re
from pathlib import Path

import pytest


def parse_env_example(env_file_path: Path) -> dict[str, str]:
    """Parse .env.example and extract all environment variables.

    Args:
        env_file_path: Path to the .env.example file

    Returns:
        Dictionary of environment variable names to values
    """
    env_vars = {}
    with open(env_file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                match = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', line)
                if match:
                    key, value = match.groups()
                    env_vars[key] = value
    return env_vars


def parse_docker_compose_env(docker_compose_path: Path) -> dict[str, str]:
    """Parse docker-compose.yml and extract environment variables for atlas-ui service.

    Args:
        docker_compose_path: Path to the docker-compose.yml file

    Returns:
        Dictionary of environment variable names to values
    """
    docker_env_vars = {}
    with open(docker_compose_path, 'r', encoding='utf-8') as f:
        in_atlas_ui_service = False
        in_environment_section = False

        for line in f:
            # Detect when we enter the atlas-ui service block
            if 'atlas-ui:' in line:
                in_atlas_ui_service = True
                in_environment_section = False
                continue

            # Detect when we enter another service block (exits atlas-ui)
            stripped = line.strip()
            if in_atlas_ui_service and line and stripped and not line[0].isspace() and ':' in line:
                # We've reached a new top-level section
                in_atlas_ui_service = False
                in_environment_section = False
                continue

            # Check if we're entering the environment section within atlas-ui
            if in_atlas_ui_service and 'environment:' in line:
                in_environment_section = True
                continue

            # Check if we've exited the environment section (e.g., volumes:, depends_on:)
            if in_environment_section and stripped and not stripped.startswith('-') and not stripped.startswith('#'):
                if ':' in line and not stripped.startswith('- '):
                    # We've reached a new subsection (like volumes:)
                    in_environment_section = False
                    continue

            # Parse environment variables
            if in_environment_section and stripped.startswith('- '):
                # Extract env var, handling quoted values
                env_line = stripped[2:]  # Remove "- "

                # Handle quoted environment variables
                if env_line.startswith('"'):
                    # Find the closing quote
                    end_quote = env_line.find('"', 1)
                    if end_quote != -1:
                        env_line = env_line[1:end_quote]

                match = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', env_line)
                if match:
                    key = match.group(1)
                    value = match.group(2).split('#')[0].strip()  # Remove inline comments
                    docker_env_vars[key] = value

    return docker_env_vars


def test_docker_compose_has_required_env_vars():
    """Test that docker-compose.yml includes all required environment variables from .env.example.

    This test ensures that Docker deployments have access to the same configuration
    options as local development environments.
    """
    # Get paths to the files
    repo_root = Path(__file__).parent.parent.parent
    env_example_path = repo_root / '.env.example'
    docker_compose_path = repo_root / 'docker-compose.yml'

    # Parse both files
    env_example_vars = parse_env_example(env_example_path)
    docker_compose_vars = parse_docker_compose_env(docker_compose_path)

    # Define variables that are intentionally different between .env.example and docker-compose.yml
    # These have valid Docker-specific reasons to be different or omitted
    docker_specific_exceptions = {
        'USE_MOCK_S3',  # Docker uses real MinIO, not mock S3
        'VITE_APP_NAME',  # Build-time arg, not runtime env var in docker-compose
        'VITE_FEATURE_POWERED_BY_ATLAS',  # Build-time arg, not runtime env var in docker-compose
        'VITE_FEATURE_ANIMATED_LOGO',  # Build-time arg, not runtime env var in docker-compose
        'VITE_FEATURE_RAG_CITATIONS',  # Build-time arg, not runtime env var in docker-compose
        # Note: The following are in .env.example as commented out, not as active vars,
        # so they won't appear in env_example_vars and don't need to be listed here:
        # - ATLAS_HOST (Docker-specific, set to 0.0.0.0 for container networking)
        # - Various MCP/proxy secret headers that are commented out in .env.example
    }

    # Find variables in .env.example but not in docker-compose.yml
    missing_vars = set(env_example_vars.keys()) - set(docker_compose_vars.keys()) - docker_specific_exceptions

    # Assert that no required variables are missing
    if missing_vars:
        missing_list = sorted(missing_vars)
        error_msg = (
            f"docker-compose.yml is missing {len(missing_vars)} environment variable(s) "
            f"that are defined in .env.example:\n"
            f"{', '.join(missing_list)}\n\n"
            f"Please add these to the 'environment:' section of the 'atlas-ui' service "
            f"in docker-compose.yml."
        )
        raise AssertionError(error_msg)

    # Verify that key feature flags are present (as mentioned in the issue)
    key_feature_flags = [
        'FEATURE_MARKETPLACE_ENABLED',
        'FEATURE_TOOLS_ENABLED',
        'FEATURE_FILES_PANEL_ENABLED',
        'FEATURE_RAG_ENABLED',
    ]

    for flag in key_feature_flags:
        assert flag in docker_compose_vars, (
            f"Key feature flag '{flag}' is missing from docker-compose.yml"
        )


def test_docker_compose_env_var_values_reasonable():
    """Test that environment variable values in docker-compose.yml are reasonable.

    This is a sanity check to ensure values aren't accidentally corrupted.
    """
    repo_root = Path(__file__).parent.parent.parent
    docker_compose_path = repo_root / 'docker-compose.yml'

    docker_compose_vars = parse_docker_compose_env(docker_compose_path)

    # Check that boolean feature flags have boolean-like values
    feature_flags = [k for k in docker_compose_vars.keys() if k.startswith('FEATURE_')]
    for flag in feature_flags:
        value = docker_compose_vars[flag].lower()
        assert value in ['true', 'false'], (
            f"Feature flag '{flag}' has non-boolean value: '{docker_compose_vars[flag]}'"
        )

    # Check that numeric values are numeric
    numeric_vars = ['PORT', 'AGENT_MAX_STEPS']
    for var in numeric_vars:
        if var in docker_compose_vars:
            value = docker_compose_vars[var]
            assert value.isdigit(), (
                f"Numeric variable '{var}' has non-numeric value: '{value}'"
            )


def test_docker_specific_vars_present():
    """Test that Docker-specific environment variables are correctly set.

    These variables have Docker-specific values that differ from .env.example.
    """
    repo_root = Path(__file__).parent.parent.parent
    docker_compose_path = repo_root / 'docker-compose.yml'

    docker_compose_vars = parse_docker_compose_env(docker_compose_path)

    # ATLAS_HOST should be 0.0.0.0 for Docker (allows external connections)
    assert 'ATLAS_HOST' in docker_compose_vars, (
        "ATLAS_HOST is required in docker-compose.yml for container networking"
    )
    assert docker_compose_vars['ATLAS_HOST'] == '0.0.0.0', (
        "ATLAS_HOST should be 0.0.0.0 in Docker for external access"
    )

    # MinIO/S3 configuration should be present for Docker
    s3_vars = ['S3_ENDPOINT', 'S3_BUCKET_NAME', 'S3_ACCESS_KEY', 'S3_SECRET_KEY']
    for var in s3_vars:
        assert var in docker_compose_vars, (
            f"S3 configuration variable '{var}' is required in docker-compose.yml for MinIO integration"
        )


def test_runtime_only_dockerfile_keeps_runtime_surface_small():
    """Ensure the optional runtime-only container build recipe stays minimal."""
    repo_root = Path(__file__).parent.parent.parent
    dockerfile_path = repo_root / 'Dockerfile.runtimeonly'

    if not dockerfile_path.exists():
        pytest.skip(
            "Dockerfile.runtimeonly not present in this checkout "
            "(expected when running tests inside a container image that does "
            "not COPY top-level Dockerfiles)."
        )

    dockerfile_content = dockerfile_path.read_text(encoding='utf-8')

    # Split into stages and parse each stage's FROM line once:
    # `FROM [--flag=...] <image> [AS <name>]` (flags such as --platform).
    chunks = re.split(r'^(?=FROM\s)', dockerfile_content, flags=re.M | re.I)[1:]
    stages = []  # (FROM line, image, lower-cased name or '', stage text)
    for chunk in chunks:
        from_line = chunk.splitlines()[0]
        match = re.fullmatch(r'FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*', from_line, re.I)
        assert match, f"Unparsed FROM line: {from_line}"
        stages.append((from_line, match.group(1), (match.group(2) or '').lower(), chunk))

    # All stages must use Chainguard images for a minimal CVE surface.
    # Match by registry prefix so the recipe can later pin to a specific
    # tag or digest without breaking this assertion.
    non_chainguard = [line for line, image, _, _ in stages if not image.startswith('cgr.dev/chainguard/')]
    assert not non_chainguard, f"Every stage must use a cgr.dev/chainguard/ image: {non_chainguard}"
    assert 'FROM cgr.dev/chainguard/node:' in dockerfile_content, (
        "Frontend build stage must use a Chainguard Node image"
    )

    # Runtime image should copy only built frontend assets, not the full frontend source tree.
    assert 'COPY --from=frontend-build /app/frontend/dist /app/atlas/static' in dockerfile_content

    # Runtime recipe should avoid pulling in extra top-level development/test trees.
    for excluded_copy in ('COPY docs/', 'COPY test/', 'COPY scripts/', 'COPY mocks/'):
        assert excluded_copy not in dockerfile_content, (
            f"Runtime-only image should not include '{excluded_copy}'"
        )

    # Runtime install must tolerate Python upper-bound constraints in transitive deps.
    assert '--ignore-requires-python ".[mcp-demos]"' in dockerfile_content

    # Join continuation lines so each instruction is one string.
    instructions = dockerfile_content.replace('\\\n', ' ').splitlines()
    # The build stage is found by its `AS` name; the final stage is the last
    # one, which is what `docker build` outputs by default.
    names = [name for _, _, name, _ in stages]
    assert 'python-build' in names, f"Expected a 'python-build' stage, found {names}"
    build_stage = stages[names.index('python-build')][3]
    final_from, final_image, _, final_stage = stages[-1]

    def apk_packages(stage):
        """Package names from a stage's `apk add` commands (flags dropped)."""
        packages = []
        for command in re.findall(r'apk add ([^&;\n]*)', stage.replace('\\\n', ' ')):
            packages += [word for word in command.split() if not word.startswith('-')]
        return packages

    final_packages = apk_packages(final_stage)

    # The final stage is not a -dev image, keeps a shell (hooks and agent-portal
    # commands can be shell scripts), and has no package manager or build tools.
    assert '-dev' not in final_image, f"Final stage must not use a -dev image: {final_from}"
    assert {'bash', 'busybox'} <= set(final_packages), (
        f"Final stage must install bash and busybox; it installs {final_packages}"
    )
    build_tools = {'build-base', 'gcc', 'clang', 'make', 'git', 'apk-tools'}
    denied = [p for p in final_packages if p in build_tools or p.endswith('-dev')]
    assert not denied, f"Final stage must not install build tools: {denied}"
    assert re.search(r'apk del .*\bapk-tools\b', final_stage.replace('\\\n', ' ')), (
        "Final stage must remove apk-tools"
    )

    # One ARG sets the Python for both stages; the venv only runs on the Python
    # it was built with.
    assert re.search(r'^ARG PYTHON_VERSION=3\.\d+$', dockerfile_content, re.M), (
        "Declare a global `ARG PYTHON_VERSION=3.X` default before the first FROM"
    )
    for label, stage in (('python-build', build_stage), ('final', final_stage)):
        assert re.search(r'^ARG PYTHON_VERSION$', stage, re.M), (
            f"The {label} stage must redeclare ARG PYTHON_VERSION"
        )
        assert 'python-${PYTHON_VERSION}' in apk_packages(stage), (
            f"The {label} stage must install the python-${{PYTHON_VERSION}} package"
        )
    assert 'RUN python${PYTHON_VERSION} -m venv /app/.venv' in build_stage, (
        "Build the venv with python${PYTHON_VERSION}"
    )
    assert 'sys.version_info >= (3, 11)' in build_stage, (
        "Build stage must check PYTHON_VERSION against pyproject.toml's >=3.11"
    )
    assert not re.search(r'\bpython-?3\.\d+', build_stage + final_stage), (
        "Use ${PYTHON_VERSION}, not a hard-coded Python version"
    )

    # The app is owned by nonroot via COPY --chown and runs as nonroot. A
    # `RUN chown -R` layer would store every file under /app a second time.
    assert 'COPY --from=python-build --chown=nonroot:nonroot /app /app' in final_stage, (
        "Final stage must copy /app from python-build with --chown=nonroot:nonroot"
    )
    assert re.search(r'^USER nonroot$', final_stage, re.M), "Final stage must run as nonroot"
    assert not any(
        line.startswith('RUN') and 'chown -R' in line for line in instructions
    ), "Use COPY --chown instead of a RUN chown -R layer"


def test_use_new_frontend_flag_is_gone():
    """Guard against quiet reintroduction of the removed USE_NEW_FRONTEND flag.

    The flag was always true and the skip-build branch it guarded referenced a
    frontend that no longer exists. The flag and its old-frontend code path were
    removed in PR #865; this test keeps them from sneaking back in.

    Two layers are checked: (1) the literal env-var name must not reappear in
    any config or startup file, and (2) the startup scripts must not contain a
    skip-build early-return path (the "Skipping frontend build" message was the
    old code path's signature). All files checked here are COPY'd into the test
    container by Dockerfile-test, so this guard runs in both local and CI runs.
    """
    repo_root = Path(__file__).parent.parent.parent

    must_be_present = [
        repo_root / '.env.example',
        repo_root / 'docker-compose.yml',
        repo_root / 'agent_start.sh',
        repo_root / 'ps_agent_start.ps1',
    ]

    for file_path in must_be_present:
        assert file_path.exists(), f"Expected file not found: {file_path}"
        content = file_path.read_text(encoding='utf-8')
        assert 'USE_NEW_FRONTEND' not in content, (
            f"'USE_NEW_FRONTEND' was found in {file_path.name} — the flag was "
            f"removed in PR #865 and should not be reintroduced."
        )

    # The startup scripts must not contain a skip-build early-return path —
    # "Skipping frontend build" was the old code path's signature message and
    # catching it prevents the removed behavior from returning under any name.
    for script_path in (repo_root / 'agent_start.sh', repo_root / 'ps_agent_start.ps1'):
        assert script_path.exists(), f"Expected startup script not found: {script_path}"
        script_content = script_path.read_text(encoding='utf-8')
        assert 'Skipping frontend build' not in script_content, (
            f"A skip-build path was found in {script_path.name} — the frontend "
            f"should always be built (PR #865 removed the skip branch)."
        )


def test_use_new_frontend_flag_is_gone_in_dockerfile_test():
    """Guard against the flag reappearing in Dockerfile-test.

    Dockerfile-test is COPY'd into the test container by this PR, so this guard
    runs in both local and CI runs. The pytest.skip is retained as a fallback
    for environments that strip top-level Dockerfiles before testing.
    """
    repo_root = Path(__file__).parent.parent.parent
    dockerfile_path = repo_root / 'Dockerfile-test'

    if not dockerfile_path.exists():
        pytest.skip(
            "Dockerfile-test not present in this checkout (expected when "
            "running inside a container image that does not COPY it)."
        )

    content = dockerfile_path.read_text(encoding='utf-8')
    assert 'USE_NEW_FRONTEND' not in content, (
        "'USE_NEW_FRONTEND' was found in Dockerfile-test — the flag was "
        "removed in PR #865 and should not be reintroduced."
    )
