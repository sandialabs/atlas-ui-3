"""Tests for atlas-init CLI startup performance and lazy imports."""

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class TestInitCliStartup:
    """Verify atlas-init starts quickly thanks to lazy imports in __init__.py."""

    def test_atlas_init_help_completes_quickly(self):
        """atlas-init --help should complete in under 2 seconds.

        Before the lazy import fix, importing the atlas package eagerly
        loaded AtlasClient and the entire heavy dependency chain
        (SQLAlchemy, litellm, FastAPI, etc.), causing ~4s startup.
        With lazy __getattr__ in atlas/__init__.py, atlas-init only
        loads stdlib modules and starts in <0.5s.
        """
        start = time.monotonic()
        result = subprocess.run(
            [sys.executable, "-m", "atlas.init_cli", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        elapsed = time.monotonic() - start

        assert result.returncode == 0, f"atlas-init --help failed: {result.stderr}"
        assert "atlas-init" in result.stdout
        assert elapsed < 2.0, (
            f"atlas-init --help took {elapsed:.2f}s (expected <2s). "
            "Lazy imports in atlas/__init__.py may have regressed."
        )

    def test_atlas_package_does_not_eagerly_import_client(self):
        """Importing 'atlas' should not pull in atlas.atlas_client."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                ("import atlas; import sys; "
                 "assert 'atlas.atlas_client' not in sys.modules, "
                 "'atlas.atlas_client was eagerly imported'"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"atlas.atlas_client was eagerly imported: {result.stderr}"
        )

    def test_lazy_import_atlas_client_works(self):
        """from atlas import AtlasClient should still work via __getattr__."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from atlas import AtlasClient; print(AtlasClient)",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Lazy import of AtlasClient failed: {result.stderr}"
        )
        assert "AtlasClient" in result.stdout

    def test_lazy_import_chat_result_works(self):
        """from atlas import ChatResult should still work via __getattr__."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from atlas import ChatResult; print(ChatResult)",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Lazy import of ChatResult failed: {result.stderr}"
        )
        assert "ChatResult" in result.stdout

    def test_invalid_attribute_raises_error(self):
        """Accessing a nonexistent attribute should raise AttributeError."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import atlas; atlas.nonexistent_attribute",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode != 0
        assert "AttributeError" in result.stderr

    def test_version_available_without_heavy_imports(self):
        """atlas.__version__ and atlas.VERSION should be available instantly."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                ("import atlas; print(atlas.__version__); print(atlas.VERSION); "
                 "import sys; "
                 "assert 'atlas.atlas_client' not in sys.modules"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"Version access triggered heavy imports: {result.stderr}"
        )


class TestInitCliEnvFile:
    """Verify atlas-init honors --env-file flag and ATLAS_ENV_FILE env var.

    These options let users keep their .env outside the install directory
    (e.g. ~/.atlasrc) so multiple users can share one Atlas install while
    each owning their own API keys. See issue: "Specifying location for
    .env configuration file".
    """

    def _run(self, args, env=None):
        return subprocess.run(
            [sys.executable, "-m", "atlas.init_cli", *args],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

    def test_env_file_flag_writes_to_custom_path(self, tmp_path):
        target = tmp_path / "project"
        custom_env = tmp_path / "personal" / ".atlasrc"

        result = self._run([
            "--target", str(target),
            "--env-file", str(custom_env),
            "--minimal",
            "--force",
        ])

        assert result.returncode == 0, result.stderr
        assert custom_env.exists(), (
            f"Expected env file at {custom_env}, output:\n{result.stdout}"
        )
        # The default location should NOT be created when a custom path is given.
        assert not (target / ".env").exists()
        assert "ATLAS_ENV_FILE" in result.stdout

    def test_atlas_env_file_env_var_is_honored(self, tmp_path):
        target = tmp_path / "project"
        custom_env = tmp_path / "from-env" / ".atlasrc"

        env = os.environ.copy()
        env["ATLAS_ENV_FILE"] = str(custom_env)

        result = self._run([
            "--target", str(target),
            "--minimal",
            "--force",
        ], env=env)

        assert result.returncode == 0, result.stderr
        assert custom_env.exists()
        assert not (target / ".env").exists()

    def test_flag_overrides_env_var(self, tmp_path):
        target = tmp_path / "project"
        flag_env = tmp_path / "from-flag" / ".atlasrc"
        var_env = tmp_path / "from-var" / ".atlasrc"

        env = os.environ.copy()
        env["ATLAS_ENV_FILE"] = str(var_env)

        result = self._run([
            "--target", str(target),
            "--env-file", str(flag_env),
            "--minimal",
            "--force",
        ], env=env)

        assert result.returncode == 0, result.stderr
        assert flag_env.exists()
        assert not var_env.exists()

    def test_default_env_path_unchanged(self, tmp_path):
        """Without --env-file or ATLAS_ENV_FILE, .env lands in target dir."""
        target = tmp_path / "project"

        # Make sure ATLAS_ENV_FILE is not inherited from the test runner.
        env = {k: v for k, v in os.environ.items() if k != "ATLAS_ENV_FILE"}

        result = self._run([
            "--target", str(target),
            "--minimal",
            "--force",
        ], env=env)

        assert result.returncode == 0, result.stderr
        assert (target / ".env").exists()
