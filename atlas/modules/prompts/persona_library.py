"""Preconfigured persona / system-prompt library (issue #880).

Admins drop markdown files in a folder and Atlas offers them to users as
selectable system prompts, without anyone having to author their own prompt or
stand up an MCP prompt server.

File format -- YAML frontmatter plus the prompt body::

    ---
    name: Research Assistant
    description: Careful, citation-first answers
    access_group: research-team    # optional; omit to show to everyone
    allowed_data_classifications:  # optional; explicit allowlist of levels
      - UUR
      - CUI
    order: 10                      # optional sort hint
    ---
    You are a meticulous research assistant...

Everything below the frontmatter block is the prompt text. ``access_group``
accepts a single group or a list; a persona with none is visible to every
authenticated user, which keeps the "just add a file" workflow the issue asks
for from silently hiding personas behind a group nobody is in.

``allowed_data_classifications`` mirrors the field on models, MCP servers and
RAG sources (issue #1062): an explicit list of the levels a persona may be
used at, exact membership after alias resolution. The legacy single-value
``compliance_level`` maps to a one-element list when the new field is absent;
when both are present the new field wins. Unlike the other config surfaces the
value must be a list -- a scalar is a hand-authoring mistake that fails closed.
A persona that declares nothing is unavailable in a classified session.

Files are read once at startup and cached; ``reload()`` re-reads them. Every
misconfiguration it detects is reported as a :class:`PersonaDiagnostic` and
printed as one clear line per persona (also available at ``library.diagnostics``)
so a skipped or narrowed persona is never silent.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

import yaml

from atlas.core.compliance import get_compliance_manager
from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PersonaDiagnostic:
    """One detected problem with a persona markdown file.

    Misconfigured personas are skipped or narrowed at load time, which is
    invisible to the user whose persona simply never appears. The library
    collects a diagnostic for each problem so startup can print one clear line
    per persona describing exactly what was wrong (``level`` is a
    :mod:`logging` level name so the line is emitted at the same severity the
    parser would have used).
    """

    source: str
    level: str
    message: str


# PyYAML's syntax errors are multi-line (context plus caret lines) and a
# classification string can carry a newline too; collapse embedded line breaks
# so one problem stays one physical log line, whatever the source.
_LINE_BREAK_RE = re.compile(r"\s*[\r\n]+\s*")


def _one_line(message: str) -> str:
    """Flatten embedded line breaks so a diagnostic is a single log line."""
    return _LINE_BREAK_RE.sub(" ", message).strip()


def _report(
    diagnostics: Optional[List[PersonaDiagnostic]],
    level: str,
    path: Path,
    message: str,
) -> None:
    """Record one persona problem, or log it when no collector is supplied.

    ``parse_persona_file`` called on its own logs immediately; ``reload()``
    passes a list so the same problem is emitted once, from the library, as
    part of the per-persona startup report instead of mid-parse. The message is
    flattened to one line and stripped of control characters before either path
    (an unknown level name or a YAML error is echoed back, so it must not be
    able to forge a log line).
    """
    message = sanitize_for_logging(_one_line(message))
    if diagnostics is None:
        getattr(logger, level)(message)
    else:
        diagnostics.append(PersonaDiagnostic(source=path.name, level=level, message=message))


# Directory name looked up under the prompt/config search paths.
PERSONA_DIR_NAME = "personas"

# Prompt bodies are authored by admins, not users, but a runaway file would be
# sent with every chat turn -- keep it in "system prompt" territory.
MAX_PERSONA_CHARS = 100_000

# Length of the server-computed preview the list endpoint sends in place of the
# full prompt body (the picker only ever shows two clamped lines).
PREVIEW_CHARS = 160

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# Documentation that lives alongside the personas is not itself a persona.
IGNORED_STEMS = {"readme", "index"}


def _is_persona_file(path: Path) -> bool:
    return not path.name.startswith((".", "_")) and path.stem.lower() not in IGNORED_STEMS


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "persona"


def _normalize_groups(raw: Any) -> List[str]:
    """Coerce ``access_group``/``access_groups`` into a list of group names."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, Sequence):
        return [str(g).strip() for g in raw if str(g).strip()]
    return [str(raw).strip()]


class Persona:
    """A single preconfigured system prompt loaded from disk."""

    def __init__(
        self,
        persona_id: str,
        name: str,
        content: str,
        description: str = "",
        access_groups: Optional[List[str]] = None,
        order: int = 1000,
        source: str = "",
        compliance_level: Optional[str] = None,
        allowed_data_classifications: Optional[List[str]] = None,
    ):
        self.id = persona_id
        self.name = name
        self.content = content
        self.description = description
        self.access_groups = access_groups or []
        self.order = order
        self.source = source
        # Legacy single level, kept for the picker's badge and older clients.
        self.compliance_level = compliance_level
        # Effective explicit allowlist (issue #1062) or None when undeclared.
        self.allowed_data_classifications = allowed_data_classifications

    def to_dict(self, include_content: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "access_groups": list(self.access_groups),
            "compliance_level": self.compliance_level,
            # Always present, matching the other resource payloads and the
            # client's classificationsOf contract: an explicit null means the
            # persona declares no classifications.
            "allowed_data_classifications": (
                list(self.allowed_data_classifications)
                if self.allowed_data_classifications is not None
                else None
            ),
        }
        if include_content:
            data["content"] = self.content
        else:
            # The picker shows at most two clamped lines; shipping the full
            # body (up to MAX_PERSONA_CHARS) per persona per page load is waste.
            data["preview"] = self.content[:PREVIEW_CHARS] + (
                "..." if len(self.content) > PREVIEW_CHARS else ""
            )
        return data


def _canonicalize_classifications(
    manager,
    entries: Sequence[str],
    path: Path,
    diagnostics: Optional[List[PersonaDiagnostic]],
) -> List[str]:
    """Canonicalize an allowlist, naming each unknown entry in a report.

    ``ComplianceLevelManager.validate_classifications`` drops unknown names but
    logs one generic "invalid compliance level" line that never says *which*
    entry was wrong. Reporting each unknown name here, before the valid entries
    reach the manager, is what turns a silent narrowing into an actionable
    startup line -- and keeps the manager from warning a second time. The entry
    is echoed with ``!r`` so an invisible or bidirectional character is shown as
    an escape instead of being silently stripped.
    """
    canonical: List[str] = []
    for entry in entries:
        if manager.is_valid_level(entry):
            resolved = manager.get_canonical_name(entry) or entry
            if resolved not in canonical:
                canonical.append(resolved)
        else:
            _report(
                diagnostics,
                "warning",
                path,
                f"Persona {path.name}: allowed_data_classifications entry "
                f"{entry!r} is not a configured data classification; dropping it "
                f"so the persona is not approved for that level.",
            )
    return canonical


def parse_persona_file(
    path: Path, diagnostics: Optional[List[PersonaDiagnostic]] = None
) -> Optional[Persona]:
    """Parse one markdown persona file, or return None if it is unusable.

    When ``diagnostics`` is supplied every problem is appended to it instead of
    being logged mid-parse, so :meth:`PersonaLibrary.reload` can print one clear
    line per persona (and its collector is exposed as ``library.diagnostics``).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        _report(diagnostics, "error", path, f"Persona {path.name}: could not read the file: {e}")
        return None

    metadata: Dict[str, Any] = {}
    body = raw
    match = _FRONTMATTER_RE.match(raw)
    if match:
        try:
            parsed = yaml.safe_load(match.group(1))
        except yaml.YAMLError as e:
            # Fail closed: keeping the body with empty metadata would drop the
            # access_group and publish a gated persona to everyone.
            _report(
                diagnostics,
                "error",
                path,
                f"Persona {path.name}: invalid YAML frontmatter, skipping the file: {e}",
            )
            return None
        if parsed is not None and not isinstance(parsed, dict):
            # Same failure mode as broken YAML: the access_group is unreadable,
            # so the only safe default is "visible to no one".
            _report(
                diagnostics,
                "error",
                path,
                f"Persona {path.name}: frontmatter is not a mapping, skipping the file",
            )
            return None
        if isinstance(parsed, dict):
            metadata = parsed
        body = match.group(2)

    content = body.strip()
    if not content:
        _report(
            diagnostics,
            "warning",
            path,
            f"Persona {path.name}: no prompt text below the frontmatter, skipping",
        )
        return None
    if len(content) > MAX_PERSONA_CHARS:
        _report(
            diagnostics,
            "warning",
            path,
            f"Persona {path.name}: prompt is {len(content)} chars, truncating to {MAX_PERSONA_CHARS}",
        )
        content = content[:MAX_PERSONA_CHARS]

    name = str(metadata.get("name") or path.stem.replace("_", " ").replace("-", " ").title()).strip()
    persona_id = _slugify(str(metadata.get("id") or path.stem))

    try:
        order = int(metadata.get("order", 1000))
    except (TypeError, ValueError):
        order = 1000

    # Validated on load like the classifications of a model or MCP server:
    # aliases canonicalize and an unknown level is dropped with a warning so a
    # typo can only narrow what a persona is approved for, never widen it.
    manager = get_compliance_manager()
    compliance_level = None
    allowed_data_classifications = None
    legacy_raw = metadata.get("compliance_level")
    if "allowed_data_classifications" in metadata:
        # Frontmatter is hand-authored, so the value must be an explicit list:
        # a scalar (including the tempting `allowed_data_classifications: CUI`)
        # or any other shape is a configuration error that fails closed rather
        # than being read as a one-element allowlist. The new field takes
        # precedence over a legacy level, even when it is malformed: a bad new
        # value must not silently fall back to broader access.
        raw_new = metadata.get("allowed_data_classifications")
        if isinstance(raw_new, (list, tuple)):
            entries = []
            dropped = 0
            for entry in raw_new:
                if isinstance(entry, str) and entry.strip():
                    # Stored stripped: a padded "Internal " must not reach the
                    # unknown-level drop after passing the guard.
                    entries.append(entry.strip())
                else:
                    # An unquoted YAML 1.1 value such as `- NO` parses as a bool,
                    # and `- 5` as an int; both would otherwise vanish silently.
                    dropped += 1
            if dropped:
                _report(
                    diagnostics,
                    "warning",
                    path,
                    f"Persona {path.name}: ignored {dropped} non-string or empty "
                    f"allowed_data_classifications entry(ies); quote each level "
                    f"name on its own '- Level' line.",
                )
        else:
            _report(
                diagnostics,
                "warning",
                path,
                f"Persona {path.name}: allowed_data_classifications must be a YAML "
                f"list of level names (one '- Level' line each); treating the "
                f"persona as approved for no classified session.",
            )
            entries = []
        allowed_data_classifications = _canonicalize_classifications(
            manager, entries, path, diagnostics
        )
        if legacy_raw:
            _report(
                diagnostics,
                "warning",
                path,
                f"Persona {path.name}: both allowed_data_classifications and the "
                f"deprecated compliance_level are set; allowed_data_classifications "
                f"is used.",
            )
    elif legacy_raw:
        if isinstance(legacy_raw, (list, tuple)):
            # A list here is a mistake: str() would turn it into a bogus level
            # name. Point the admin at the field that takes a list and fail
            # closed rather than publishing the repr as a level.
            _report(
                diagnostics,
                "warning",
                path,
                f"Persona {path.name}: compliance_level takes a single level; put a "
                f"list under allowed_data_classifications instead.",
            )
        else:
            # Backward compatibility: a legacy single level is a one-element list.
            legacy_value = str(legacy_raw)
            if manager.is_valid_level(legacy_value):
                compliance_level = manager.get_canonical_name(legacy_value) or legacy_value
            else:
                _report(
                    diagnostics,
                    "warning",
                    path,
                    f"Persona {path.name}: compliance_level {legacy_value!r} is not a "
                    f"configured data classification; ignoring it so the persona is "
                    f"approved for no classified session.",
                )
            if compliance_level:
                allowed_data_classifications = [compliance_level]

    return Persona(
        persona_id=persona_id,
        name=name,
        content=content,
        description=str(metadata.get("description") or "").strip(),
        access_groups=_normalize_groups(
            metadata.get("access_group", metadata.get("access_groups"))
        ),
        order=order,
        source=path.name,
        compliance_level=compliance_level,
        allowed_data_classifications=allowed_data_classifications,
    )


class PersonaLibrary:
    """Loads persona markdown files from the first directory that exists."""

    def __init__(self, search_paths: Sequence[Path]):
        self._search_paths = [Path(p) for p in search_paths]
        self._personas: Optional[List[Persona]] = None
        self._diagnostics: List[PersonaDiagnostic] = []

    @property
    def search_paths(self) -> List[Path]:
        return list(self._search_paths)

    @property
    def diagnostics(self) -> List[PersonaDiagnostic]:
        """Problems found the last time the files were read, one per problem."""
        return list(self._diagnostics)

    def _report_diagnostics(self, diagnostics: List[PersonaDiagnostic]) -> None:
        """Print one clear line per persona describing each detected problem.

        The parser stays quiet while a collector is in play, so this is the
        single place a misconfiguration reaches the startup log. Always called
        from a ``finally`` in :meth:`reload`, so the collector reflects the run
        that just happened even if it raised part way through.
        """
        self._diagnostics = list(diagnostics)
        # Count only the entries that a WARNING threshold actually shows, so the
        # header never announces a problem whose detail line is filtered out.
        problem_count = sum(1 for d in diagnostics if d.level != "info")
        if problem_count:
            logger.warning(
                "Preconfigured personas: %d configuration problem(s) detected.",
                problem_count,
            )
        for diagnostic in diagnostics:
            getattr(logger, diagnostic.level)("%s", diagnostic.message)

    def reload(self) -> List[Persona]:
        """Re-read persona files from disk and return them."""
        personas: List[Persona] = []
        seen_ids: Dict[str, str] = {}
        diagnostics: List[PersonaDiagnostic] = []

        try:
            for directory in self._search_paths:
                try:
                    if not directory.is_dir():
                        continue
                except OSError:  # pragma: no cover - defensive, e.g. permission denied
                    continue

                for path in sorted(directory.glob("*.md")):
                    if not _is_persona_file(path):
                        continue
                    persona = parse_persona_file(path, diagnostics)
                    if persona is None:
                        continue
                    if persona.id in seen_ids:
                        # First directory wins, mirroring the config override
                        # layering; surface it so a duplicate is not silent.
                        _report(
                            diagnostics,
                            "info",
                            path,
                            f"Persona {path.name}: id '{persona.id}' is already "
                            f"provided by {seen_ids[persona.id]}; this file is "
                            f"shadowed and was not loaded.",
                        )
                        continue
                    seen_ids[persona.id] = str(path)
                    personas.append(persona)

                # Only the first existing directory is authoritative, so an admin
                # who creates their own folder is not padded with the packaged
                # samples.
                if personas or any(_is_persona_file(p) for p in directory.glob("*.md")):
                    break

            personas.sort(key=lambda p: (p.order, p.name.lower()))
        finally:
            # Buffered diagnostics must survive an unexpected failure mid-loop,
            # and must not leave a previous run's report in place.
            self._report_diagnostics(diagnostics)

        self._personas = personas
        logger.info("Loaded %d preconfigured persona(s)", len(personas))
        return personas

    def all_personas(self) -> List[Persona]:
        if self._personas is None:
            self.reload()
        return list(self._personas or [])

    def get(self, persona_id: str) -> Optional[Persona]:
        for persona in self.all_personas():
            if persona.id == persona_id:
                return persona
        return None

    async def personas_for_user(
        self,
        user_email: str,
        group_check: Callable[[str, str], Awaitable[bool]],
    ) -> List[Persona]:
        """Return the personas ``user_email`` is allowed to see.

        Group membership is resolved once per request: each distinct group
        named by any persona is checked exactly once, concurrently, and the
        results are reused across personas. A group whose check raises is
        treated as "not a member" (fail closed).
        """
        personas = self.all_personas()
        groups = {group for p in personas for group in p.access_groups}

        async def check(group: str) -> tuple:
            try:
                return group, await group_check(user_email, group)
            except Exception as e:  # fail closed on authorization errors
                logger.error("Group check failed for group '%s': %s", group, e)
                return group, False

        results = await asyncio.gather(*(check(g) for g in groups)) if groups else []
        membership = dict(results)
        return [
            p for p in personas
            if not p.access_groups or any(membership.get(g) for g in p.access_groups)
        ]

    async def persona_for_user(
        self,
        persona_id: str,
        user_email: str,
        group_check: Callable[[str, str], Awaitable[bool]],
    ) -> Optional[Persona]:
        """Return the persona ``persona_id`` if ``user_email`` may see it.

        Looks the persona up by id *before* authorizing, so fetching one
        persona costs at most its own access-group checks rather than a
        round-trip per gated persona in the folder. Returns None for both
        "missing" and "not allowed" so callers cannot tell them apart.
        """
        persona = self.get(persona_id)
        if persona is None:
            return None
        if not persona.access_groups:
            return persona
        for group in persona.access_groups:
            try:
                if await group_check(user_email, group):
                    return persona
            except Exception as e:  # fail closed on authorization errors
                logger.error(
                    "Group check failed for persona '%s' group '%s': %s",
                    persona.id, group, e,
                )
        return None


def default_search_paths(config_manager) -> List[Path]:
    """Persona directories, user overrides first then packaged defaults."""
    atlas_root = Path(config_manager._atlas_root)
    project_root = atlas_root.parent

    settings = config_manager.app_settings
    configured = getattr(settings, "personas_dir", "") or ""

    candidates: List[Path] = []
    if configured:
        configured_path = Path(configured)
        if configured_path.is_absolute():
            candidates.append(configured_path)
        else:
            candidates.append(project_root / configured_path)

    # The conventional home is the user config dir (APP_CONFIG_DIR, default
    # "config/") alongside mcp.json and the other admin-authored files,
    # mirroring the two-layer config lookup: config/personas/ overrides the
    # packaged samples below.
    config_dir = Path(settings.app_config_dir)
    if not config_dir.is_absolute():
        config_dir = project_root / config_dir
    candidates.append(config_dir / PERSONA_DIR_NAME)

    candidates.append(atlas_root / "config" / "prompts" / PERSONA_DIR_NAME)

    seen = set()
    resolved: List[Path] = []
    for path in candidates:
        if path not in seen:
            seen.add(path)
            resolved.append(path)
    return resolved


_library: Optional[PersonaLibrary] = None


def get_persona_library() -> PersonaLibrary:
    """Process-wide persona library, built from the active config manager."""
    global _library
    if _library is None:
        from atlas.modules.config.config_manager import config_manager

        _library = PersonaLibrary(default_search_paths(config_manager))
    return _library


def _compliance_feature_enabled() -> bool:
    """Whether compliance-level enforcement is on; unreadable means off."""
    try:
        from atlas.modules.config.config_manager import config_manager

        return bool(
            getattr(
                config_manager.app_settings,
                "feature_compliance_levels_enabled",
                False,
            )
        )
    except Exception:  # pragma: no cover - defensive; a broken flag must not break chat
        return False


def _compliance_allows(
    user_level: Optional[str],
    classifications: Optional[List[str]],
) -> bool:
    """Strict mirror of the client picker's filtering for tools and prompts:
    with a compliance filter active a persona needs a level in its
    ``allowed_data_classifications`` (a persona that declares nothing is
    hidden); with no filter, every persona goes. Exact membership after alias
    resolution -- ``allowed_with`` never widens it (issue #1032).
    """
    if not user_level:
        return True
    return get_compliance_manager().classification_permits(user_level, classifications)


async def resolve_persona_prompt(
    persona_id: Optional[str],
    user_email: str,
    group_check: Optional[Callable[[str, str], Awaitable[bool]]] = None,
    compliance_level_filter: Optional[str] = None,
) -> Optional[str]:
    """Return the prompt text for ``persona_id``, if this user may use it.

    Personas are admin-authored server-side content, so the chat path sends only
    an id and resolves the text here -- re-checking the access group so a
    hand-crafted client cannot select a persona it was never offered. When the
    compliance-levels feature is on, the turn's compliance filter is enforced
    the same strict way the picker filters: a persona whose
    ``allowed_data_classifications`` the filter does not include (including a
    persona that declares nothing under an active filter) resolves to None.
    Returns None (i.e. "use the default system prompt") for an unknown,
    unauthorized, compliance-blocked, or unreadable persona rather than failing
    the turn.
    """
    if not persona_id:
        return None

    if group_check is None:
        from atlas.core.auth import is_user_in_group

        group_check = is_user_in_group

    try:
        persona = await get_persona_library().persona_for_user(
            persona_id, user_email, group_check
        )
    except Exception as e:
        logger.error("Failed to load personas: %s", e, exc_info=True)
        return None

    if persona is None:
        logger.warning("Ignoring unknown or unauthorized persona id for this user")
        return None

    if compliance_level_filter and _compliance_feature_enabled():
        # The raw filter goes straight to the shared rule: an undefined filter
        # level matches no classification (fail closed), rather than skipping
        # the check. classification_permits resolves aliases itself.
        if not _compliance_allows(
            compliance_level_filter, persona.allowed_data_classifications
        ):
            logger.warning(
                "Ignoring persona id blocked by the active compliance filter"
            )
            return None

    return persona.content


async def resolve_chat_system_prompt(
    inline_prompt: Optional[str],
    persona_id: Optional[str],
    user_email: str,
    custom_prompts_effective: bool,
    group_check: Optional[Callable[[str, str], Awaitable[bool]]] = None,
    compliance_level_filter: Optional[str] = None,
) -> Optional[str]:
    """Decide the system-prompt override for one chat turn (the /ws branch).

    An inline ``custom_system_prompt`` (issue #153) is honored only when the
    custom-prompt feature is on -- the flag is the single source of truth, so a
    stale or hand-crafted client cannot smuggle one in. A persona id (issue
    #880) is feature-independent admin-authored content and applies whenever no
    inline prompt took effect; the text is resolved server-side after
    re-checking the access group and the turn's compliance filter. Returns None
    for "use the default prompt".
    """
    prompt = inline_prompt if custom_prompts_effective else None
    if prompt is None and persona_id:
        prompt = await resolve_persona_prompt(
            persona_id, user_email, group_check, compliance_level_filter
        )
    return prompt


def reset_persona_library() -> None:
    """Drop the cached library (tests / config reloads)."""
    global _library
    _library = None
