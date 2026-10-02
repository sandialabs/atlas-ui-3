#!/usr/bin/env python3
"""Release metadata helpers for the weekly automated release workflow.

The workflow calls this script instead of inlining the file edits, so the
version bump, "anything to release?" check, and release-note extraction are
all exercised by the PR validation script rather than only in CI YAML.

Subcommands
-----------
current
    Print the version currently in ``atlas/version.py``.
check
    Exit 0 when ``CHANGELOG.md`` has an ``## [Unreleased]`` section with at
    least one entry; exit 1 when that section is empty (nothing to release);
    exit 2 on a malformed changelog.
plan --bump {major,minor,patch}
    Print the next version derived from the current version.
apply --version X.Y.Z [--date YYYY-MM-DD] [--force]
    Bump ``atlas/version.py`` and ``pyproject.toml``, and reshape
    ``CHANGELOG.md`` so ``## [Unreleased]`` becomes
    ``## [X.Y.Z] - YYYY-MM-DD`` with a fresh empty ``[Unreleased]`` above it.
notes --version X.Y.Z
    Print the changelog section for ``X.Y.Z`` (the GitHub Release body).
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import sys

VERSION_FILE = pathlib.Path("atlas/version.py")
PYPROJECT_FILE = pathlib.Path("pyproject.toml")
CHANGELOG_FILE = pathlib.Path("CHANGELOG.md")

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
UNRELEASED_RE = re.compile(r"^## \[Unreleased\][ \t]*$", re.MULTILINE)
NEXT_RELEASED_RE = re.compile(r"^## \[", re.MULTILINE)


class ReleaseBumpError(Exception):
    """A malformed input file or invalid argument."""


def read_current_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise ReleaseBumpError(f"no VERSION assignment found in {VERSION_FILE}")
    version = match.group(1)
    if not SEMVER_RE.match(version):
        raise ReleaseBumpError(f"invalid version {version!r} in {VERSION_FILE}")
    return version


def _unreleased_body(text: str) -> str | None:
    """Return the text between `## [Unreleased]` and the next `## [` heading."""
    match = UNRELEASED_RE.search(text)
    if not match:
        return None
    rest = text[match.end():]
    next_match = NEXT_RELEASED_RE.search(rest)
    return rest[: next_match.start()] if next_match else rest


def has_unreleased_changes() -> bool:
    body = _unreleased_body(CHANGELOG_FILE.read_text(encoding="utf-8"))
    if body is None:
        raise ReleaseBumpError(
            f"{CHANGELOG_FILE} is missing a '## [Unreleased]' section"
        )
    # Strip HTML comments, including multi-line placeholders, so a commented
    # template does not read as a release-worthy change.
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    for line in body.splitlines():
        if line.strip():
            return True
    return False


def next_version(current: str, bump: str) -> str:
    if not SEMVER_RE.match(current):
        raise ReleaseBumpError(f"invalid current version {current!r}")
    major, minor, patch = (int(part) for part in current.split("."))
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseBumpError(f"unknown bump kind {bump!r}")


def apply_bump(version: str, date: str, force: bool) -> None:
    if not SEMVER_RE.match(version):
        raise ReleaseBumpError(f"version must look like X.Y.Z, got {version!r}")
    if not force and not has_unreleased_changes():
        raise ReleaseBumpError(
            "refusing to bump: the '## [Unreleased]' section has no entries"
        )

    old = read_current_version()

    auth_text, count = re.subn(
        r'^VERSION\s*=\s*"[^"]+"',
        f'VERSION = "{version}"',
        VERSION_FILE.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ReleaseBumpError(f"expected exactly one VERSION line in {VERSION_FILE}")

    pyproject_text, count = re.subn(
        r'(?m)^version\s*=\s*"[^"]+"',
        f'version = "{version}"',
        PYPROJECT_FILE.read_text(encoding="utf-8"),
        count=1,
    )
    if count != 1:
        raise ReleaseBumpError(
            f"expected exactly one top-level version line in {PYPROJECT_FILE}"
        )

    changelog_text = CHANGELOG_FILE.read_text(encoding="utf-8")
    match = UNRELEASED_RE.search(changelog_text)
    if not match:
        raise ReleaseBumpError(f"{CHANGELOG_FILE} has no '## [Unreleased]' section")
    reshaped = (
        changelog_text[: match.start()]
        + "## [Unreleased]\n\n"
        + f"## [{version}] - {date}"
        + changelog_text[match.end():]
    )

    VERSION_FILE.write_text(auth_text, encoding="utf-8")
    PYPROJECT_FILE.write_text(pyproject_text, encoding="utf-8")
    CHANGELOG_FILE.write_text(reshaped, encoding="utf-8")

    print(f"{VERSION_FILE}: {old} -> {version}")
    print(f"{PYPROJECT_FILE}: {old} -> {version}")
    print(f"{CHANGELOG_FILE}: [Unreleased] -> [{version}] - {date}")


def release_notes(version: str) -> str:
    text = CHANGELOG_FILE.read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n.*?(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise ReleaseBumpError(f"no '## [{version}]' section in {CHANGELOG_FILE}")
    return match.group(0).rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("current", help="print the current version")
    sub.add_parser("check", help="exit 1 when there is nothing to release")

    plan = sub.add_parser("plan", help="print the next version")
    plan.add_argument("--bump", required=True, choices=["major", "minor", "patch"])

    apply_cmd = sub.add_parser("apply", help="apply the version bump")
    apply_cmd.add_argument("--version", required=True)
    apply_cmd.add_argument(
        "--date", default=datetime.date.today().isoformat()
    )
    apply_cmd.add_argument(
        "--force",
        action="store_true",
        help="bump even when the Unreleased section is empty",
    )

    notes = sub.add_parser("notes", help="print the release notes")
    notes.add_argument("--version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "current":
            print(read_current_version())
        elif args.command == "check":
            if has_unreleased_changes():
                print("Unreleased section has entries")
                return 0
            print("Unreleased section is empty; nothing to release")
            return 1
        elif args.command == "plan":
            print(next_version(read_current_version(), args.bump))
        elif args.command == "apply":
            apply_bump(args.version, args.date, args.force)
        elif args.command == "notes":
            sys.stdout.write(release_notes(args.version))
    except ReleaseBumpError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
