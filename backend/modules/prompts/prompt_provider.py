"""Prompt provider module for loading and caching prompt templates.

Centralizes prompt path resolution & template retrieval so core services stay
focused on orchestration/business logic.
"""
from __future__ import annotations

import os
import logging
from typing import Optional, Dict

from modules.config import ConfigManager

logger = logging.getLogger(__name__)


class PromptProvider:
    """Loads and caches prompt templates based on application configuration."""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self._cache: Dict[str, str] = {}
        # Resolve base path (relative paths resolved against repo root)
        app_settings = self.config_manager.app_settings
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
        base_candidate = app_settings.prompt_base_path
        if not os.path.isabs(base_candidate):
            self.base_path = os.path.join(repo_root, base_candidate)
        else:
            self.base_path = base_candidate

    def _load_template(self, filename: str) -> Optional[str]:
        cache_key = filename
        if cache_key in self._cache:
            return self._cache[cache_key]
        path = os.path.join(self.base_path, filename)
        if not os.path.exists(path):
            logger.warning("Prompt template not found: %s", path)
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self._cache[cache_key] = content
            return content
        except Exception as e:  # pragma: no cover
            logger.error("Failed reading prompt template %s: %s", path, e)
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

    def clear_cache(self) -> None:
        """Clear in-memory prompt cache (e.g., after config reload)."""
        self._cache.clear()
