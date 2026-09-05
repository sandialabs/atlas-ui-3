"""Preconfigured persona / system-prompt library (issue #880).

Admins drop markdown files in a folder and Atlas offers them to users as
selectable system prompts, without anyone having to author their own prompt or
stand up an MCP prompt server.

File format -- YAML frontmatter plus the prompt body::

    ---
    name: Research Assistant
    description: Careful, citation-first answers
    access_group: research-team    # optional; omit to show to everyone
    order: 10                      # optional sort hint
    ---
    You are a meticulous research assistant...

Everything below the frontmatter block is the prompt text. ``access_group``
accepts a single group or a list; a persona with none is visible to every
authenticated user, which keeps the "just add a file" workflow the issue asks
for from silently hiding personas behind a group nobody is in.

Files are read once at startup and cached; ``reload()`` re-reads them.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

import yaml

from atlas.core.compliance import get_compliance_manager

logger = logging.getLogger(__name__)

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
    ):
        self.id = persona_id
        self.name = name
        self.content = content
        self.description = description
        self.access_groups = access_groups or []
        self.order = order
        self.source = source
        self.compliance_level = compliance_level

    def to_dict(self, include_content: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "access_groups": list(self.access_groups),
            "compliance_level": self.compliance_level,
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


def parse_persona_file(path: Path) -> Optional[Persona]:
    """Parse one markdown persona file, or return None if it is unusable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Could not read persona file %s: %s", path, e)
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
            logger.error(
                "Persona %s: invalid YAML frontmatter, skipping the file: %s",
                path.name, e,
            )
            return None
        if parsed is not None and not isinstance(parsed, dict):
            # Same failure mode as broken YAML: the access_group is unreadable,
            # so the only safe default is "visible to no one".
            logger.error(
                "Persona %s: frontmatter is not a mapping, skipping the file",
                path.name,
            )
            return None
        if isinstance(parsed, dict):
            metadata = parsed
        body = match.group(2)

    content = body.strip()
    if not content:
        logger.warning("Persona %s: no prompt text below the frontmatter, skipping", path.name)
        return None
    if len(content) > MAX_PERSONA_CHARS:
        logger.warning(
            "Persona %s: prompt is %d chars, truncating to %d",
            path.name, len(content), MAX_PERSONA_CHARS,
        )
        content = content[:MAX_PERSONA_CHARS]

    name = str(metadata.get("name") or path.stem.replace("_", " ").replace("-", " ").title()).strip()
    persona_id = _slugify(str(metadata.get("id") or path.stem))

    try:
        order = int(metadata.get("order", 1000))
    except (TypeError, ValueError):
        order = 1000

    # Validated on load like the compliance level of a model or MCP server:
    # aliases canonicalize and an unknown level becomes None with a warning.
    compliance_level = None
    compliance_raw = metadata.get("compliance_level")
    if compliance_raw:
        compliance_level = get_compliance_manager().validate_compliance_level(
            str(compliance_raw), context=f"persona file {path.name}"
        )

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
    )


class PersonaLibrary:
    """Loads persona markdown files from the first directory that exists."""

    def __init__(self, search_paths: Sequence[Path]):
        self._search_paths = [Path(p) for p in search_paths]
        self._personas: Optional[List[Persona]] = None

    @property
    def search_paths(self) -> List[Path]:
        return list(self._search_paths)

    def reload(self) -> List[Persona]:
        """Re-read persona files from disk and return them."""
        personas: List[Persona] = []
        seen_ids: Dict[str, str] = {}

        for directory in self._search_paths:
            try:
                if not directory.is_dir():
                    continue
            except OSError:  # pragma: no cover - defensive, e.g. permission denied
                continue

            for path in sorted(directory.glob("*.md")):
                if not _is_persona_file(path):
                    continue
                persona = parse_persona_file(path)
                if persona is None:
                    continue
                if persona.id in seen_ids:
                    # First directory wins, mirroring the config override layering.
                    logger.info(
                        "Persona id '%s' from %s is shadowed by %s",
                        persona.id, path, seen_ids[persona.id],
                    )
                    continue
                seen_ids[persona.id] = str(path)
                personas.append(persona)

            # Only the first existing directory is authoritative, so an admin who
            # creates their own folder is not padded with the packaged samples.
            if personas or any(_is_persona_file(p) for p in directory.glob("*.md")):
                break

        personas.sort(key=lambda p: (p.order, p.name.lower()))
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


def _compliance_allows(user_level: Optional[str], persona_level: Optional[str]) -> bool:
    """Strict mirror of the client picker's filtering for tools and prompts:
    with a compliance filter active a persona needs a level the filter allows
    (a level-less persona is hidden); with no filter, every persona goes.
    """
    if not user_level:
        return True
    if not persona_level:
        return False
    return get_compliance_manager().is_accessible(user_level, persona_level)


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
    the same strict way the picker filters: a persona whose level the filter
    does not allow (including a level-less persona under an active filter)
    resolves to None. Returns None (i.e. "use the default system prompt") for an
    unknown, unauthorized, compliance-blocked, or unreadable persona rather
    than failing the turn.
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
        validated = get_compliance_manager().validate_compliance_level(
            compliance_level_filter, context="chat request"
        )
        if validated and not _compliance_allows(validated, persona.compliance_level):
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
