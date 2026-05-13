"""Prompt provider module for loading and caching prompt templates.

Centralizes prompt path resolution & template retrieval so core services stay
focused on orchestration/business logic.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

from atlas.modules.config import ConfigManager

logger = logging.getLogger(__name__)


class PromptProvider:
    """Loads and caches prompt templates based on application configuration."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self._cache: Dict[str, str] = {}
        self._base_paths = self._resolve_base_paths()

    def _resolve_base_paths(self) -> list[Path]:
        """Resolve prompt search paths.

        Follows the same override convention as other Atlas config assets:
        user config directory first, packaged defaults second.
        """
        base_candidate = Path(self.config_manager.app_settings.prompt_base_path)
        atlas_root = self.config_manager._atlas_root
        project_root = atlas_root.parent

        candidates = []
        if base_candidate.is_absolute():
            candidates.append(base_candidate)
        else:
            candidates.extend([
                project_root / base_candidate,
                atlas_root / "config" / "prompts",
            ])

        seen = set()
        resolved: list[Path] = []
        for path in candidates:
            if path not in seen:
                seen.add(path)
                resolved.append(path)
        return resolved

    def _load_template(self, filename: str) -> Optional[str]:
        cache_key = filename
        if cache_key in self._cache:
            return self._cache[cache_key]

        for base_path in self._base_paths:
            path = base_path / filename
            if not path.exists():
                continue
            try:
                content = path.read_text(encoding="utf-8")
                self._cache[cache_key] = content
                return content
            except Exception as e:  # pragma: no cover
                logger.error("Failed reading prompt template %s: %s", path, e)
                return None

        logger.warning(
            "Prompt template not found: %s (searched: %s)",
            filename,
            [str(path) for path in self._base_paths],
        )
        return None

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
