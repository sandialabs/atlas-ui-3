"""``git_clone`` PAT redaction.

These tests exercise the wrapper script's post-clone behavior — rewriting
``origin`` to strip the PAT and unsetting any credential helpers — so the
PAT does not survive in ``<repo>/.git/config``.

We clone from a local bare repo via ``file://`` so no network is required.
The wrapper script is run with the parent process's interpreter rather
than under the kernel sandbox: the redaction logic is independent of the
sandbox layers and we can validate it on hosts that don't support
unprivileged userns.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


HAS_GIT = shutil.which("git") is not None


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    subprocess.run(
        ["git", "-C", str(src), "config", "user.email", "test@example"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(src), "config", "user.name", "test"],
        check=True,
    )
    (src / "README.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(src), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(src), "commit", "-q", "-m", "init"],
        check=True,
    )
    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(src), str(bare)],
        check=True,
    )
    return bare


@pytest.mark.skipif(not HAS_GIT, reason="git is required for clone tests")
def test_pat_is_not_persisted_in_repo_config(bare_repo: Path, tmp_path: Path):
    """The wrapper must rewrite origin to the credential-free URL."""
    from git_clone import _wrapper_script

    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = "myrepo"

    # Use a distinctive sentinel as the "PAT" so we can grep .git/config.
    pat = "PAT_SENTINEL_DO_NOT_LEAK"
    repo_url = f"file://{bare_repo}"
    script = _wrapper_script(repo_url, "HEAD", target, depth=1)

    env = dict(os.environ)
    env["GIT_PAT"] = pat
    rc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(workspace),
        env=env,
    ).returncode
    # file:// transport ignores credentials, so clone should succeed
    # regardless of the (bogus) PAT.
    assert rc == 0, "clone should succeed for file:// transport"

    cloned = workspace / target
    assert (cloned / "README.md").exists()

    # The critical assertion: PAT must not have leaked into .git/config.
    config_text = (cloned / ".git" / "config").read_text()
    assert pat not in config_text, (
        f".git/config contains the PAT sentinel:\n{config_text}"
    )

    # And the remote URL must be the credential-free form.
    remotes = subprocess.run(
        ["git", "-C", str(cloned), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert pat not in remotes
    assert remotes == repo_url
