#!/usr/bin/env python3
"""Changelog fragment helpers.

Normal PRs add a small ``changes/<id>.<type>.md`` fragment instead of editing
``CHANGELOG.md`` directly, so independent PRs no longer conflict on the same
region at the top of the changelog. The release workflows compose the
fragments into ``## [Unreleased]`` before ``release_bump.py apply`` reshapes
that heading into a released section.

Subcommands
-----------
validate [--dir changes]
    Validate every fragment under the directory (filename, type, non-empty
    body). ``changes/README.md`` is ignored. Exit 0 when clean, 1 on the
    first problem, 2 on a usage/IO error.
check [--dir changes]
    Exit 0 when at least one fragment is present, 1 when none are.
collect [--dir changes] [--changelog CHANGELOG.md] [--dry-run]
    Compose every fragment into ``## [Unreleased]`` grouped by type, then
    delete the consumed fragment files. ``--dry-run`` prints the composed
    block without writing anything.
guard --changed-files FILE [--allow-changelog]
    CI guard: fail when a normal PR edits ``CHANGELOG.md``. The changed-file
    list is one path per line (``git diff --name-only``). Release branches
    pass ``--allow-changelog`` because the bump must rewrite the changelog.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from dataclasses import dataclass

FRAGMENTS_DIR = pathlib.Path("changes")
CHANGELOG_FILE = pathlib.Path("CHANGELOG.md")
README_NAME = "README.md"

# Order matters: it is the order the type sections are emitted in.
TYPES = ("breaking", "feature", "fix", "security", "internal")
TYPE_HEADINGS = {
    "breaking": "Breaking Changes",
    "feature": "Features",
    "fix": "Fixes",
    "security": "Security",
    "internal": "Internal",
}
FRAGMENT_RE = re.compile(
    r"^(?P<id>\d+)\.(?P<type>" + "|".join(TYPES) + r")\.md$"
)
UNRELEASED_RE = re.compile(r"^## \[Unreleased\][ \t]*$", re.MULTILINE)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
BULLET_RE = re.compile(r"^\s*[-*]\s+")


class FragmentError(Exception):
    """A malformed fragment or changelog."""


@dataclass(frozen=True)
class Fragment:
    id: str
    type: str
    path: pathlib.Path
    body: str


def parse_fragment(path: pathlib.Path) -> Fragment:
    """Parse one fragment file, raising FragmentError when it is unusable."""
    match = FRAGMENT_RE.match(path.name)
    if not match:
        raise FragmentError(
            f"{path.name}: expected <id>.<type>.md with type in "
            f"{', '.join(TYPES)}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FragmentError(f"{path.name}: could not read: {exc}") from exc

    body = text.strip()
    if not COMMENT_RE.sub("", body).strip():
        raise FragmentError(f"{path.name}: fragment body is empty")

    return Fragment(id=match.group("id"), type=match.group("type"), path=path, body=body)


def iter_fragments(directory: pathlib.Path | str) -> list[Fragment]:
    """Return every fragment in the directory, sorted by id then type order.

    A malformed fragment raises rather than being skipped: silently dropping
    a release note is worse than failing the release.
    """
    dir_path = pathlib.Path(directory)
    if not dir_path.is_dir():
        return []
    fragments = []
    for path in sorted(dir_path.iterdir()):
        if not path.is_file() or path.name == README_NAME:
            continue
        fragments.append(parse_fragment(path))
    fragments.sort(key=lambda frag: (int(frag.id), TYPES.index(frag.type), frag.path.name))
    return fragments


def validate_directory(directory: pathlib.Path | str) -> int:
    dir_path = pathlib.Path(directory)
    if not dir_path.is_dir():
        print(f"{dir_path}: no fragment directory; nothing to validate")
        return 0
    fragments = iter_fragments(dir_path)
    for frag in fragments:
        print(f"{frag.path.name}: ok ({frag.type})")
    print(f"Validated {len(fragments)} changelog fragment(s).")
    return 0


def compose(fragments: list[Fragment]) -> str:
    """Render fragments into Keep-a-Changelog type sections."""
    sections = []
    for type_name in TYPES:
        group = [frag for frag in fragments if frag.type == type_name]
        if not group:
            continue
        lines = [f"### {TYPE_HEADINGS[type_name]}"]
        for frag in group:
            body_lines = frag.body.strip("\n").splitlines()
            first = BULLET_RE.sub("", body_lines[0]).strip()
            lines.append(f"- **#{frag.id}:** {first}")
            for continuation in body_lines[1:]:
                lines.append(f"  {continuation}" if continuation.strip() else "")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def insert_unreleased(text: str, block: str) -> str:
    """Insert composed sections immediately below ``## [Unreleased]``."""
    match = UNRELEASED_RE.search(text)
    if not match:
        raise FragmentError(f"{CHANGELOG_FILE} has no '## [Unreleased]' section")
    rest = text[match.end():].lstrip("\n")
    return text[: match.end()] + "\n\n" + block + "\n\n" + rest


def collect(
    directory: pathlib.Path | str,
    changelog: pathlib.Path | str,
    dry_run: bool = False,
) -> int:
    fragments = iter_fragments(directory)
    if not fragments:
        print("No changelog fragments to collect.")
        return 0
    block = compose(fragments)
    if dry_run:
        sys.stdout.write(block + "\n")
        return 0

    changelog_path = pathlib.Path(changelog)
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise FragmentError(f"{changelog_path}: could not read: {exc}") from exc
    changelog_path.write_text(insert_unreleased(text, block), encoding="utf-8")

    for frag in fragments:
        frag.path.unlink()
    names = ", ".join(frag.path.name for frag in fragments)
    print(f"Composed {len(fragments)} fragment(s) into {changelog_path}: {names}")
    return 0


def guard(changed_files: pathlib.Path | str, allow_changelog: bool) -> int:
    try:
        changed = [
            line.strip()
            for line in pathlib.Path(changed_files).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError) as exc:
        print(f"could not read changed-files list: {exc}", file=sys.stderr)
        return 2

    if changelog_file_name() not in changed:
        print("CHANGELOG.md untouched; fragment guard passed.")
        return 0
    if allow_changelog:
        print("Release branch exempt from the fragment guard; CHANGELOG.md may be rewritten.")
        return 0

    print(
        "CHANGELOG.md was edited directly. It is generated at release time from "
        "changes/ fragments, so normal PRs must not touch it -- add "
        "changes/<id>.<type>.md (types: " + ", ".join(TYPES) + ") instead. "
        "Release branches (release/*, hotfix/*) are exempt.",
        file=sys.stderr,
    )
    return 1


def changelog_file_name() -> str:
    return CHANGELOG_FILE.as_posix()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate fragment files")
    validate.add_argument("--dir", default=str(FRAGMENTS_DIR))

    check = sub.add_parser("check", help="exit 0 when fragments exist")
    check.add_argument("--dir", default=str(FRAGMENTS_DIR))

    collect_cmd = sub.add_parser("collect", help="compose fragments into CHANGELOG.md")
    collect_cmd.add_argument("--dir", default=str(FRAGMENTS_DIR))
    collect_cmd.add_argument("--changelog", default=str(CHANGELOG_FILE))
    collect_cmd.add_argument("--dry-run", action="store_true")

    guard_cmd = sub.add_parser("guard", help="fail when a normal PR edits CHANGELOG.md")
    guard_cmd.add_argument("--changed-files", required=True)
    guard_cmd.add_argument(
        "--allow-changelog",
        action="store_true",
        help="exempt release branches that rewrite the changelog",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            return validate_directory(args.dir)
        if args.command == "check":
            fragments = iter_fragments(args.dir)
            if fragments:
                print(f"{len(fragments)} changelog fragment(s) present")
                return 0
            print("No changelog fragments present")
            return 1
        if args.command == "collect":
            return collect(args.dir, args.changelog, args.dry_run)
        if args.command == "guard":
            return guard(args.changed_files, args.allow_changelog)
    except FragmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
