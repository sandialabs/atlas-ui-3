"""Prompt provider module for loading and caching prompt templates.

Centralizes prompt path resolution & template retrieval so core services stay
focused on orchestration/business logic.

Templates ship as package data inside ``atlas/prompts/`` and are loaded via
``importlib.resources``. Operators may override the location by setting
``prompt_base_path`` to a filesystem directory (absolute, or relative to the
current working directory).
"""
from __future__ import annotations

import logging
import os
from importlib.resources import as_file, files
from importlib.resources.abc import Traversable
from typing import Dict, Optional

from atlas.modules.config import ConfigManager

logger = logging.getLogger(__name__)

# Sentinel value of ``prompt_base_path`` that means "use the bundled package
# resources shipped under ``atlas/prompts/``".
_BUNDLED_SENTINEL = "prompts"


class PromptProvider:
    """Loads and caches prompt templates based on application configuration."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self._cache: Dict[str, str] = {}
        base_candidate = (self.config_manager.app_settings.prompt_base_path or "").strip()

        # Default (or explicit "prompts") → bundled package resources.
        # Anything else → filesystem path, resolved against CWD if relative.
        self._fs_base_path: Optional[str]
        self._resource_root: Optional[Traversable]
        if not base_candidate or base_candidate == _BUNDLED_SENTINEL:
            self._fs_base_path = None
            self._resource_root = files("atlas").joinpath("prompts")
        else:
            self._fs_base_path = os.path.abspath(base_candidate)
            self._resource_root = None

    @property
    def base_path(self) -> str:
        """Human-readable base location for logging/debugging."""
        if self._fs_base_path is not None:
            return self._fs_base_path
        # Resolve the resource root to a concrete path when possible (best-effort,
        # purely informational — the actual loader uses importlib.resources).
        try:
            with as_file(self._resource_root) as p:  # type: ignore[arg-type]
                return str(p)
        except Exception:
            return "<atlas package resources: prompts>"

    def _load_template(self, filename: str) -> Optional[str]:
        cache_key = filename
        if cache_key in self._cache:
            return self._cache[cache_key]

        if self._fs_base_path is not None:
            path = os.path.join(self._fs_base_path, filename)
            if not os.path.exists(path):
                logger.warning("Prompt template not found: %s", path)
                return None
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception as e:  # pragma: no cover
                logger.error("Failed reading prompt template %s: %s", path, e)
                return None
            self._cache[cache_key] = content
            return content

        # Bundled package resource path.
        assert self._resource_root is not None
        resource = self._resource_root.joinpath(filename)
        if not resource.is_file():
            logger.warning("Prompt template not found in atlas package: prompts/%s", filename)
            return None
        try:
            content = resource.read_text(encoding="utf-8")
        except Exception as e:  # pragma: no cover
            logger.error("Failed reading bundled prompt template prompts/%s: %s", filename, e)
            return None
        self._cache[cache_key] = content
        return content

    def get_tool_synthesis_prompt(self, user_question: str) -> Optional[str]:
        """Return formatted tool synthesis prompt or None if unavailable."""
        filename = self.config_manager.app_settings.tool_synthesis_prompt_filename
        template = self._load_template(filename)
        if not template:
            return None
        try:
            return template.format(user_question=user_question.strip())
        except Exception as e:  # pragma: no cover - safeguard
            logger.warning("Formatting tool synthesis prompt failed: %s", e)
            return None

    def get_agent_reason_prompt(
        self,
        user_question: str,
        files_manifest: Optional[str] = None,
        last_observation: Optional[str] = None,
    ) -> Optional[str]:
        """Return formatted agent reason prompt text or None if unavailable.

        Expects template placeholders: {user_question}, {files_manifest}, {last_observation}
        Missing values are rendered as empty strings.
        """
        filename = self.config_manager.app_settings.agent_reason_prompt_filename
        template = self._load_template(filename)
        if not template:
            return None
        try:
            return template.format(
                user_question=(user_question or "").strip(),
                files_manifest=(files_manifest or ""),
                last_observation=(last_observation or ""),
            )
        except Exception as e:  # pragma: no cover
            logger.warning("Formatting agent reason prompt failed: %s", e)
            return None

    def get_agent_observe_prompt(
        self,
        user_question: str,
        tool_summaries: str,
        step: int,
    ) -> Optional[str]:
        """Return formatted agent observe prompt text or None if unavailable.

        Expects template placeholders: {user_question}, {tool_summaries}, {step}
        """
        filename = self.config_manager.app_settings.agent_observe_prompt_filename
        template = self._load_template(filename)
        if not template:
            return None
        try:
            return template.format(
                user_question=(user_question or "").strip(),
                tool_summaries=(tool_summaries or ""),
                step=step,
            )
        except Exception as e:  # pragma: no cover
            logger.warning("Formatting agent observe prompt failed: %s", e)
            return None

    def get_system_prompt(self, user_email: Optional[str] = None) -> Optional[str]:
        """Return formatted system prompt text or None if unavailable.

        Expects template placeholder: {user_email}
        Missing values are rendered as empty strings.
        """
        filename = self.config_manager.app_settings.system_prompt_filename
        template = self._load_template(filename)
        if not template:
            return None
        try:
            return template.format(
                user_email=(user_email or ""),
            )
        except Exception as e:  # pragma: no cover
            logger.warning("Formatting system prompt failed: %s", e)
            return None

    def clear_cache(self) -> None:
        """Clear in-memory prompt cache (e.g., after config reload)."""
        self._cache.clear()
