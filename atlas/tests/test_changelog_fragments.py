"""Unit tests for scripts/changelog_fragments.py.

Normal PRs add ``changes/<id>.<type>.md`` fragments instead of editing
CHANGELOG.md; the release workflows compose them before the version bump and
CI validates them, so the behaviour is covered here in CI as well as in the PR
validation sandbox.
"""

from __future__ import annotations

import pathlib

import pytest

from scripts import changelog_fragments


def _fragment(tmp_path, name: str, body: str) -> pathlib.Path:
    changes = tmp_path / "changes"
    changes.mkdir(exist_ok=True)
    path = changes / name
    path.write_text(body, encoding="utf-8")
    return path


def _changelog(tmp_path, unreleased_body: str = "", history: str = "") -> pathlib.Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n\n"
        + unreleased_body
        + "\n## [0.6.0] - 2026-09-22\n\n"
        + history
        + "- Older change.\n",
        encoding="utf-8",
    )
    return path


def test_validate_accepts_each_type(tmp_path, capsys):
    for type_name in changelog_fragments.TYPES:
        _fragment(tmp_path, f"12.{type_name}.md", "A change.\n")
    assert changelog_fragments.main(["validate", "--dir", str(tmp_path / "changes")]) == 0
    assert "Validated 5 changelog fragment(s)." in capsys.readouterr().out


def test_validate_rejects_unknown_type(tmp_path, capsys):
    _fragment(tmp_path, "12.chore.md", "A change.\n")
    assert changelog_fragments.main(["validate", "--dir", str(tmp_path / "changes")]) == 2
    assert "expected <id>.<type>.md" in capsys.readouterr().err


def test_validate_rejects_non_numeric_id(tmp_path):
    _fragment(tmp_path, "feature.thing.md", "A change.\n")
    assert changelog_fragments.main(["validate", "--dir", str(tmp_path / "changes")]) == 2


def test_validate_rejects_empty_body(tmp_path):
    _fragment(tmp_path, "12.fix.md", "   \n<!-- placeholder -->\n")
    assert changelog_fragments.main(["validate", "--dir", str(tmp_path / "changes")]) == 2


def test_validate_ignores_readme(tmp_path, capsys):
    _fragment(tmp_path, "README.md", "# How to use fragments\n")
    assert changelog_fragments.main(["validate", "--dir", str(tmp_path / "changes")]) == 0
    assert "Validated 0 changelog fragment(s)." in capsys.readouterr().out


def test_validate_missing_directory_is_a_noop(tmp_path):
    assert changelog_fragments.main(["validate", "--dir", str(tmp_path / "nope")]) == 0


def test_check_reports_presence(tmp_path, capsys):
    assert changelog_fragments.main(["check", "--dir", str(tmp_path / "changes")]) == 1
    _fragment(tmp_path, "12.feature.md", "A change.\n")
    assert changelog_fragments.main(["check", "--dir", str(tmp_path / "changes")]) == 0
    assert "1 changelog fragment(s) present" in capsys.readouterr().out


def test_compose_groups_by_type_and_orders_sections(tmp_path):
    fragments = changelog_fragments.iter_fragments(
        _make_dir(
            tmp_path,
            {
                "30.fix.md": "Fixed the thing.",
                "10.feature.md": "Added the thing.",
                "20.breaking.md": "Removed the old thing.",
            },
        )
    )
    block = changelog_fragments.compose(fragments)
    assert block.index("### Breaking Changes") < block.index("### Features")
    assert block.index("### Features") < block.index("### Fixes")
    assert "- **#20:** Removed the old thing." in block
    assert "- **#10:** Added the thing." in block


def test_compose_strips_an_existing_bullet_marker(tmp_path):
    fragments = changelog_fragments.iter_fragments(
        _make_dir(tmp_path, {"10.fix.md": "- Fixed the thing.\n"})
    )
    assert "- **#10:** Fixed the thing." in changelog_fragments.compose(fragments)


def test_compose_indents_continuation_lines(tmp_path):
    fragments = changelog_fragments.iter_fragments(
        _make_dir(tmp_path, {"10.fix.md": "Fixed the thing.\n\nMore detail here.\n"})
    )
    block = changelog_fragments.compose(fragments)
    assert "- **#10:** Fixed the thing." in block
    assert "  More detail here." in block


def test_collect_inserts_and_deletes_fragments(tmp_path):
    _fragment(tmp_path, "12.feature.md", "Added the thing.\n")
    _fragment(tmp_path, "13.fix.md", "Fixed the thing.\n")
    changelog = _changelog(tmp_path)

    assert changelog_fragments.collect(tmp_path / "changes", changelog) == 0

    text = changelog.read_text(encoding="utf-8")
    assert text.index("## [Unreleased]") < text.index("### Features")
    assert "### Features" in text and "### Fixes" in text
    assert "- **#12:** Added the thing." in text
    assert "- **#13:** Fixed the thing." in text
    # Released history and the fresh-empty structure survive.
    assert "## [0.6.0] - 2026-09-22" in text
    # Consumed fragments are gone.
    assert not (tmp_path / "changes" / "12.feature.md").exists()
    assert not (tmp_path / "changes" / "13.fix.md").exists()


def test_collect_preserves_existing_unreleased_entries(tmp_path):
    _fragment(tmp_path, "12.feature.md", "Added the thing.\n")
    changelog = _changelog(tmp_path, unreleased_body="### PR #900 - 2026-10-01\n- Direct entry.\n")

    changelog_fragments.collect(tmp_path / "changes", changelog)

    text = changelog.read_text(encoding="utf-8")
    assert "- **#12:** Added the thing." in text
    assert "### PR #900 - 2026-10-01" in text
    assert "- Direct entry." in text
    # Fragments render above the legacy direct block.
    assert text.index("### Features") < text.index("### PR #900 - 2026-10-01")


def test_collect_dry_run_writes_nothing(tmp_path, capsys):
    fragment = _fragment(tmp_path, "12.feature.md", "Added the thing.\n")
    changelog = _changelog(tmp_path)
    before = changelog.read_text(encoding="utf-8")

    assert changelog_fragments.collect(tmp_path / "changes", changelog, dry_run=True) == 0

    assert fragment.exists()
    assert changelog.read_text(encoding="utf-8") == before
    assert "- **#12:** Added the thing." in capsys.readouterr().out


def test_collect_without_fragments_is_a_noop(tmp_path, capsys):
    changelog = _changelog(tmp_path)
    before = changelog.read_text(encoding="utf-8")
    assert changelog_fragments.collect(tmp_path / "changes", changelog) == 0
    assert changelog.read_text(encoding="utf-8") == before
    assert "No changelog fragments to collect." in capsys.readouterr().out


def test_collect_refuses_a_malformed_fragment(tmp_path):
    _fragment(tmp_path, "12.chore.md", "Not a valid type.\n")
    changelog = _changelog(tmp_path)
    with pytest.raises(changelog_fragments.FragmentError):
        changelog_fragments.collect(tmp_path / "changes", changelog)


def test_collect_errors_without_unreleased_heading(tmp_path):
    _fragment(tmp_path, "12.feature.md", "Added the thing.\n")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## [0.6.0] - 2026-09-22\n", encoding="utf-8")
    with pytest.raises(changelog_fragments.FragmentError):
        changelog_fragments.collect(tmp_path / "changes", changelog)


def test_guard_passes_when_changelog_untouched(tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_text("atlas/app.py\nchanges/12.feature.md\n", encoding="utf-8")
    assert changelog_fragments.guard(changed, allow_changelog=False) == 0


def test_guard_fails_on_direct_changelog_edit(tmp_path, capsys):
    changed = tmp_path / "changed.txt"
    changed.write_text("CHANGELOG.md\natlas/app.py\n", encoding="utf-8")
    assert changelog_fragments.guard(changed, allow_changelog=False) == 1
    assert "CHANGELOG.md was edited directly" in capsys.readouterr().err


def test_guard_allows_release_branches(tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_text("CHANGELOG.md\n", encoding="utf-8")
    assert changelog_fragments.guard(changed, allow_changelog=True) == 0


def test_guard_errors_when_changed_file_is_missing(tmp_path):
    assert changelog_fragments.guard(tmp_path / "missing.txt", allow_changelog=False) == 2


def test_main_returns_2_on_malformed_fragment(tmp_path, capsys):
    _fragment(tmp_path, "12.chore.md", "A change.\n")
    assert (
        changelog_fragments.main(["collect", "--dir", str(tmp_path / "changes"),
                                  "--changelog", str(_changelog(tmp_path))])
        == 2
    )
    assert "error:" in capsys.readouterr().err


def _make_dir(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    changes = tmp_path / "changes"
    changes.mkdir(exist_ok=True)
    for name, body in files.items():
        (changes / name).write_text(body, encoding="utf-8")
    return changes
