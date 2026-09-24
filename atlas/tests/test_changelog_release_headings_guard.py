from pathlib import Path

from scripts.check_changelog_release_headings import main


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_guard_accepts_matching_release_headings(tmp_path):
    base = _write(
        tmp_path / "base.md",
        "# Changelog\n\n## [Unreleased]\n\n## [0.6.0] - 2026-09-22\n\n## [0.5.0] - 2026-08-01\n",
    )
    head = _write(
        tmp_path / "head.md",
        "# Changelog\n\n## [Unreleased]\n\n### PR #971 - 2026-09-24\n- note\n\n## [0.6.0] - 2026-09-22\n\n## [0.5.0] - 2026-08-01\n",
    )

    assert main(["check", base, head]) == 0


def test_guard_rejects_missing_release_heading(tmp_path):
    base = _write(
        tmp_path / "base.md",
        "# Changelog\n\n## [Unreleased]\n\n## [0.6.0] - 2026-09-22\n\n## [0.5.0] - 2026-08-01\n",
    )
    head = _write(
        tmp_path / "head.md",
        "# Changelog\n\n## [Unreleased]\n\n## [0.6.0] - 2026-09-22\n",
    )

    assert main(["check", base, head]) == 1
