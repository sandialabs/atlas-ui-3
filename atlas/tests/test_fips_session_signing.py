"""Regression checks for FIPS-compatible session signing."""

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOTS = ("alembic", "atlas", "docs", "mocks", "scripts", "test", "test_e2e")
FIPS_WRAPPER = Path("atlas/core/session_middleware.py")
STARLETTE_SESSIONS = "starlette.middleware.sessions"
HASHLIB = "hashlib"
ITS_DANGEROUS = "itsdangerous"
SIGNER_CLASSES = ("Signer", "TimestampSigner")
SERIALIZER_CLASSES = (
    "Serializer",
    "TimedSerializer",
    "URLSafeSerializer",
    "URLSafeTimedSerializer",
    "JSONWebSignatureSerializer",
    "TimedJSONWebSignatureSerializer",
)


def _python_sources():
    candidates = set(_tracked_python_files())
    candidates.update(REPO_ROOT.glob("*.py"))
    for source_root in SOURCE_ROOTS:
        path = REPO_ROOT / source_root
        if path.exists():
            candidates.update(path.rglob("*.py"))
    yield from sorted(p for p in candidates if p.is_file())


def _tracked_python_files():
    try:
        listing = subprocess.run(
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        REPO_ROOT / name
        for name in listing.decode("utf-8").split("\0")
        if name
    ]


class _ModuleImports:
    """Local names bound to hashlib / itsdangerous modules and members."""

    def __init__(self, tree):
        self.module_aliases = {}
        self.member_imports = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    self.module_aliases.setdefault(top, set()).add(
                        alias.asname or top
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                members = self.member_imports.setdefault(top, {})
                for alias in node.names:
                    members[alias.asname or alias.name] = alias.name

    @property
    def hashlib_names(self):
        return self.module_aliases.get(HASHLIB, set())

    @property
    def hashlib_new_names(self):
        return {
            local
            for local, original in self.member_imports.get(HASHLIB, {}).items()
            if original == "new"
        }

    @property
    def itsdangerous_module_names(self):
        return self.module_aliases.get(ITS_DANGEROUS, set())

    @property
    def itsdangerous_member_names(self):
        return self.member_imports.get(ITS_DANGEROUS, {})


def _is_sha1_string_call(node):
    args = node.args
    if not args or not isinstance(args[0], ast.Constant):
        return False
    if not isinstance(args[0].value, str) or args[0].value.strip().lower() != "sha1":
        return False
    return not any(
        keyword.arg == "usedforsecurity"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is False
        for keyword in node.keywords
    )


def _digest_method_is_sha1(node):
    for keyword in node.keywords:
        if keyword.arg == "digest_method":
            source = ast.unparse(keyword.value).lower().replace("_", "")
            return "sha1" in source
    return False


def _itsdangerous_violation(node, imports):
    func = node.func
    class_name = None
    if isinstance(func, ast.Name):
        class_name = imports.member_imports.get(ITS_DANGEROUS, {}).get(func.id)
    elif (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in imports.itsdangerous_module_names
    ):
        class_name = func.attr
    if class_name not in SIGNER_CLASSES + SERIALIZER_CLASSES:
        return None
    if class_name in SIGNER_CLASSES:
        digest_keywords = [
            keyword
            for keyword in node.keywords
            if keyword.arg == "digest_method"
        ]
        if not digest_keywords or all(
            isinstance(keyword.value, ast.Constant) and keyword.value.value is None
            for keyword in digest_keywords
        ):
            return (
                f"{class_name} without digest_method defaults to itsdangerous's "
                "lazy SHA-1 signer; pass digest_method=hashlib.sha256"
            )
        if _digest_method_is_sha1(node):
            return "SHA-1 signer digest"
        return None
    for keyword in node.keywords:
        if keyword.arg == "signer_kwargs" and isinstance(keyword.value, ast.Dict):
            if not any(
                isinstance(key, ast.Constant) and key.value == "digest_method"
                for key in keyword.value.keys
            ):
                return (
                    f"{class_name} signer_kwargs lacks explicit digest_method; "
                    "itsdangerous defaults to a lazy SHA-1 digest"
                )
            return None
    return (
        f"{class_name} without signer_kwargs defaults to a SHA-1 signer; "
        "pass signer_kwargs with an explicit non-SHA-1 digest_method"
    )


def test_python_sources_do_not_use_sha1_for_session_signing():
    violations = []

    for path in sorted(_python_sources()):
        relative_path = path.relative_to(REPO_ROOT)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative_path))
        imports = _ModuleImports(tree)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom) and node.module == STARLETTE_SESSIONS
            ) or (
                isinstance(node, ast.Import)
                and any(alias.name == STARLETTE_SESSIONS for alias in node.names)
            ):
                if relative_path != FIPS_WRAPPER:
                    violations.append(
                        f"{relative_path}:{node.lineno}: import SessionMiddleware from "
                        "atlas.core.session_middleware instead"
                    )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "starlette.middleware"
                and any(alias.name == "sessions" for alias in node.names)
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: import SessionMiddleware from "
                    "atlas.core.session_middleware instead"
                )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("import_module", "__import__")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == STARLETTE_SESSIONS
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: import SessionMiddleware from "
                    "atlas.core.session_middleware instead"
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == HASHLIB
                and any(alias.name == "sha1" for alias in node.names)
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: direct hashlib.sha1 import"
                )
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in imports.hashlib_names
                and node.attr == "sha1"
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: direct hashlib.sha1 usage"
                )
            elif (
                isinstance(node, ast.Call)
                and _is_sha1_string_call(node)
                and (
                    (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr == "new"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id in imports.hashlib_names
                    )
                    or (
                        isinstance(node.func, ast.Name)
                        and imports.hashlib_new_names
                        and node.func.id in imports.hashlib_new_names
                    )
                )
            ):
                violations.append(
                    f"{relative_path}:{node.lineno}: hashlib.new('sha1') usage"
                )
            elif isinstance(node, ast.Call):
                violation = _itsdangerous_violation(node, imports)
                if violation:
                    violations.append(f"{relative_path}:{node.lineno}: {violation}")
                elif _digest_method_is_sha1(node):
                    violations.append(
                        f"{relative_path}:{node.lineno}: SHA-1 signer digest"
                    )

    assert not violations, "Prohibited SHA-1 session-signing patterns:\n" + "\n".join(
        violations
    )
