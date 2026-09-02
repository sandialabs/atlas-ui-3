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

import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

import yaml

logger = logging.getLogger(__name__)

# Directory name looked up under the prompt/config search paths.
PERSONA_DIR_NAME = "personas"

# Prompt bodies are authored by admins, not users, but a runaway file would be
# sent with every chat turn -- keep it in "system prompt" territory.
MAX_PERSONA_CHARS = 100_000

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
    ):
        self.id = persona_id
        self.name = name
        self.content = content
        self.description = description
        self.access_groups = access_groups or []
        self.order = order
        self.source = source

    def to_dict(self, include_content: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "access_groups": list(self.access_groups),
        }
        if include_content:
            data["content"] = self.content
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
            if isinstance(parsed, dict):
                metadata = parsed
            elif parsed is not None:
                logger.warning(
                    "Persona %s: frontmatter is not a mapping, ignoring it", path.name
                )
        except yaml.YAMLError as e:
            # Keep the body: a typo in the metadata should not delete the persona,
            # it should fall back to filename-derived defaults.
            logger.error("Persona %s: invalid YAML frontmatter: %s", path.name, e)
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
        """Return the personas ``user_email`` is allowed to see."""
        allowed: List[Persona] = []
        for persona in self.all_personas():
            if not persona.access_groups:
                allowed.append(persona)
                continue
            for group in persona.access_groups:
                try:
                    if await group_check(user_email, group):
                        allowed.append(persona)
                        break
                except Exception as e:  # fail closed on authorization errors
                    logger.error(
                        "Group check failed for persona '%s' group '%s': %s",
                        persona.id, group, e,
                    )
        return allowed


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

    prompt_base = Path(settings.prompt_base_path)
    if prompt_base.is_absolute():
        candidates.append(prompt_base / PERSONA_DIR_NAME)
    else:
        candidates.append(project_root / prompt_base / PERSONA_DIR_NAME)

    candidates.append(project_root / "prompts" / PERSONA_DIR_NAME)
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


def reset_persona_library() -> None:
    """Drop the cached library (tests / config reloads)."""
    global _library
    _library = None
