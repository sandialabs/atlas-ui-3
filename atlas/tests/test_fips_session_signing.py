"""Regression checks for FIPS-compatible session signing."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("alembic", "atlas", "docs", "mocks", "scripts", "test", "test_e2e")
FIPS_WRAPPER = Path("atlas/core/session_middleware.py")
STARLETTE_SESSIONS = "starlette.middleware.sessions"


def _python_sources():
    yield from REPO_ROOT.glob("*.py")
    for source_root in SOURCE_ROOTS:
        path = REPO_ROOT / source_root
        if path.exists():
            yield from path.rglob("*.py")


def test_python_sources_do_not_use_sha1_for_session_signing():
    violations = []

    for path in sorted(_python_sources()):
        relative_path = path.relative_to(REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative_path))

        for node in ast.walk(tree):
            if (
                (
                    isinstance(node, ast.ImportFrom)
                    and node.module == STARLETTE_SESSIONS
                )
                or (
                    isinstance(node, ast.Import)
                    and any(
                        alias.name == STARLETTE_SESSIONS for alias in node.names
                    )
                )
            ) and relative_path != FIPS_WRAPPER:
                violations.append(
                    f"{relative_path}:{node.lineno}: import SessionMiddleware from "
                    "atlas.core.session_middleware instead"
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "hashlib"
                and any(alias.name == "sha1" for alias in node.names)
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: direct hashlib.sha1 import"
                )
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "hashlib"
                and node.attr == "sha1"
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: direct hashlib.sha1 usage"
                )
            elif isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if (
                        keyword.arg == "digest_method"
                        and "sha1" in ast.unparse(keyword.value).lower().replace("_", "")
                    ):
                        violations.append(
                            f"{relative_path}:{node.lineno}: SHA-1 signer digest"
                        )

    assert not violations, "Prohibited SHA-1 session-signing patterns:\n" + "\n".join(
        violations
    )
