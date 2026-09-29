#!/usr/bin/env python3
"""Fail when a PR drops released CHANGELOG headings from the base branch."""

from __future__ import annotations

import pathlib
import re
import sys

HEADING_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\](?:\s+-\s+\d{4}-\d{2}-\d{2})?\s*$", re.MULTILINE)


def released_versions(text: str) -> list[str]:
    """Return released CHANGELOG version headings in file order."""
    return HEADING_RE.findall(text)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: check_changelog_release_headings.py BASE_CHANGELOG HEAD_CHANGELOG",
            file=sys.stderr,
        )
        return 2

    base_path = pathlib.Path(argv[1])
    head_path = pathlib.Path(argv[2])
    base_versions = released_versions(base_path.read_text(encoding="utf-8"))
    head_versions = set(released_versions(head_path.read_text(encoding="utf-8")))

    missing = [version for version in base_versions if version not in head_versions]
    if missing:
        formatted = ", ".join(missing)
        print(
            "CHANGELOG.md is missing released headings present on the base branch: "
            f"{formatted}",
            file=sys.stderr,
        )
        return 1

    print(f"CHANGELOG release-heading guard passed ({len(base_versions)} released headings checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
