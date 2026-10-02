"""Unit tests for scripts/release_bump.py.

The weekly release workflow drives its version bump, "anything to release?"
check, and release-note extraction through this script, so its behaviour is
covered here in CI rather than only in the PR validation sandbox.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_bump.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("release_bump", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def release_bump():
    return _load_module()


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A minimal release-state sandbox that the script runs against."""
    (tmp_path / "atlas").mkdir()
    (tmp_path / "atlas" / "version.py").write_text('VERSION = "0.6.0"\n')
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.0"\n')
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_changelog(sandbox, body: str) -> None:
    (sandbox / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n"
        + body
        + "\n## [0.6.0] - 2026-09-22\n\n### PR #900 - 2026-09-01\n- Older change.\n"
    )


def test_current_reads_version(release_bump, sandbox):
    assert release_bump.read_current_version() == "0.6.0"


def test_check_reports_populated_unreleased(release_bump, sandbox):
    _write_changelog(sandbox, "### PR #1 - 2026-10-01\n- A change.")
    assert release_bump.has_unreleased_changes() is True
    assert release_bump.main(["check"]) == 0


def test_check_reports_empty_unreleased(release_bump, sandbox):
    _write_changelog(sandbox, "")
    assert release_bump.has_unreleased_changes() is False
    assert release_bump.main(["check"]) == 1


def test_check_ignores_multiline_html_comment(release_bump, sandbox):
    _write_changelog(
        sandbox,
        "<!--\nplaceholder text\nthat spans lines\n-->\n",
    )
    assert release_bump.has_unreleased_changes() is False


def test_check_errors_when_unreleased_heading_missing(release_bump, sandbox):
    (sandbox / "CHANGELOG.md").write_text("# Changelog\n\n## [0.6.0] - 2026-09-22\n")
    assert release_bump.main(["check"]) == 2


@pytest.mark.parametrize(
    ("bump", "expected"),
    [("major", "1.0.0"), ("minor", "0.7.0"), ("patch", "0.6.1")],
)
def test_plan_follows_semver(release_bump, sandbox, bump, expected):
    assert release_bump.next_version("0.6.0", bump) == expected


def test_apply_bumps_sources_and_reshapes_changelog(release_bump, sandbox):
    _write_changelog(sandbox, "### PR #1 - 2026-10-01\n- A change.")
    assert release_bump.main(["apply", "--version", "0.7.0", "--date", "2026-10-06"]) == 0

    assert 'VERSION = "0.7.0"' in (sandbox / "atlas" / "version.py").read_text()
    assert 'version = "0.7.0"' in (sandbox / "pyproject.toml").read_text()
    changelog = (sandbox / "CHANGELOG.md").read_text()
    assert "## [0.7.0] - 2026-10-06" in changelog
    # A fresh empty Unreleased heading is inserted above the released one.
    assert changelog.index("## [Unreleased]") < changelog.index("## [0.7.0]")
    # The released history is preserved.
    assert "## [0.6.0] - 2026-09-22" in changelog


def test_apply_refuses_empty_release(release_bump, sandbox):
    _write_changelog(sandbox, "")
    assert release_bump.main(["apply", "--version", "0.7.0", "--date", "2026-10-06"]) == 2
    assert 'VERSION = "0.6.0"' in (sandbox / "atlas" / "version.py").read_text()


def test_notes_returns_only_the_requested_section(release_bump, sandbox):
    _write_changelog(sandbox, "### PR #1 - 2026-10-01\n- A change.")
    assert release_bump.main(["apply", "--version", "0.7.0", "--date", "2026-10-06"]) == 0
    notes = release_bump.release_notes("0.7.0")
    assert notes.startswith("## [0.7.0] - 2026-10-06")
    assert "A change." in notes
    assert "## [0.6.0]" not in notes


def test_notes_errors_for_unknown_version(release_bump, sandbox):
    _write_changelog(sandbox, "### PR #1 - 2026-10-01\n- A change.")
    with pytest.raises(release_bump.ReleaseBumpError):
        release_bump.release_notes("9.9.9")
