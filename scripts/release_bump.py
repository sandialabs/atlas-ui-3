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
    ``--changelog PATH`` reads from a different file, e.g. the blob at the
    merge commit rather than a possibly stale working tree.
checks --file PATH --required a,b,c
    Given ``gh pr checks --json name,bucket`` output, print ``OK``, or
    ``FAIL``/``PENDING`` with the offending check names. Required checks
    must be exactly ``pass`` -- a ``skipping`` required check is not green.
"""

from __future__ import annotations

import argparse
import datetime
import json
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


def read_pyproject_version() -> str:
    text = PYPROJECT_FILE.read_text(encoding="utf-8")
    match = re.search(
        r'(?ms)^\[project\][^\[]*?^version\s*=\s*"([^"]+)"', text
    )
    if not match:
        raise ReleaseBumpError(f"no version under [project] in {PYPROJECT_FILE}")
    return match.group(1)


def apply_bump(version: str, date: str, force: bool) -> None:
    if not SEMVER_RE.match(version):
        raise ReleaseBumpError(f"version must look like X.Y.Z, got {version!r}")
    if not force and not has_unreleased_changes():
        raise ReleaseBumpError(
            "refusing to bump: the '## [Unreleased]' section has no entries"
        )

    old = read_current_version()
    pyproject_old = read_pyproject_version()
    if pyproject_old != old:
        raise ReleaseBumpError(
            f"version drift: {VERSION_FILE} is {old} but {PYPROJECT_FILE} is "
            f"{pyproject_old}; reconcile them before releasing"
        )

    auth_text, count = re.subn(
        r'^VERSION\s*=\s*"[^"]+"',
        f'VERSION = "{version}"',
        VERSION_FILE.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ReleaseBumpError(f"expected exactly one VERSION line in {VERSION_FILE}")

    pyproject_pattern = re.compile(
        r'(?ms)(^\[project\][^\[]*?^version\s*=\s*")[^"]+(")'
    )
    pyproject_text, count = pyproject_pattern.subn(
        lambda m: f"{m.group(1)}{version}{m.group(2)}",
        PYPROJECT_FILE.read_text(encoding="utf-8"),
        count=1,
    )
    if count != 1:
        raise ReleaseBumpError(
            f"expected exactly one version line under [project] in {PYPROJECT_FILE}"
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


def release_notes(version: str, changelog_path: pathlib.Path | str = CHANGELOG_FILE) -> str:
    text = pathlib.Path(changelog_path).read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n.*?(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise ReleaseBumpError(f"no '## [{version}]' section in {changelog_path}")
    return match.group(0).rstrip() + "\n"


def check_verdict(checks: object, required: list[str]) -> str:
    """Return OK/FAIL/PENDING for a parsed ``gh pr checks --json`` payload.

    Required checks must be exactly ``pass``; a skipped required check is not
    a green gate. Any non-required check that failed or was cancelled fails
    the whole gate, and any still-pending check keeps it pending.
    """
    if not isinstance(checks, list):
        return "PENDING: no checks reported"

    by_name: dict[str, list[str]] = {}
    for check in checks:
        if not isinstance(check, dict):
            continue
        name = str(check.get("name"))
        by_name.setdefault(name, []).append(str(check.get("bucket")))

    missing = sorted(name for name in required if name not in by_name)
    failed = sorted(
        name
        for name, buckets in by_name.items()
        if any(bucket in ("fail", "cancel") for bucket in buckets)
    )
    not_pass = sorted(
        name
        for name in required
        if name in by_name
        and any(bucket not in ("pass", "pending") for bucket in by_name[name])
    )
    pending = sorted(
        name
        for name, buckets in by_name.items()
        if any(bucket == "pending" for bucket in buckets)
    )

    if failed:
        return "FAIL: failing checks: " + ", ".join(failed)
    if missing:
        return "PENDING: missing required checks: " + ", ".join(missing)
    if not_pass:
        return "FAIL: required checks not green: " + ", ".join(not_pass)
    if pending:
        return "PENDING: checks still running: " + ", ".join(pending)
    return "OK"


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
    notes.add_argument("--changelog", default=str(CHANGELOG_FILE))

    checks = sub.add_parser("checks", help="verdict for a gh pr checks JSON file")
    checks.add_argument("--file", required=True)
    checks.add_argument("--required", required=True)
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
            sys.stdout.write(release_notes(args.version, args.changelog))
        elif args.command == "checks":
            try:
                payload = json.loads(
                    pathlib.Path(args.file).read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                payload = None
            required = [name for name in args.required.split(",") if name]
            print(check_verdict(payload, required))
    except (ReleaseBumpError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
