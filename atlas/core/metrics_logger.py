"""
Metrics logging utility for tracking user activities without capturing sensitive data.

This module provides a centralized way to log user activity metrics that:
- Use the [METRIC] prefix for easy filtering
- Include the username for tracking
- Only log metadata (counts, sizes, types)
- NEVER log sensitive data like prompts, tool arguments, filenames, or error details

Usage:
    from atlas.core.metrics_logger import log_metric

    log_metric("llm_call", user_email, model="gpt-4", message_count=5)
    log_metric("tool_call", user_email, tool_name="calculator")
    log_metric("file_upload", user_email, file_size=1024, content_type="application/pdf")
    log_metric("error", user_email, error_type="rate_limit")
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def log_metric(
    event_type: str,
    user_email: Optional[str] = None,
    **kwargs: Any
) -> None:
    """
    Log a metric event for user activity tracking.

    This function respects the FEATURE_METRICS_LOGGING_ENABLED setting.
    When disabled, no metrics are logged.

    Args:
        event_type: Type of event (e.g., "llm_call", "tool_call", "file_upload", "error")
        user_email: User's email address (will be sanitized)
        **kwargs: Additional metadata to log (only non-sensitive data)
    """
    # Import here to avoid circular dependencies
    from atlas.modules.config import config_manager
    from atlas.core.log_sanitizer import sanitize_for_logging

    if not config_manager.app_settings.feature_metrics_logging_enabled:
        return

    sanitized_user = sanitize_for_logging(user_email) if user_email else "unknown"

    parts = [f"[METRIC] [{sanitized_user}] {event_type}"]

    if kwargs:
        metadata_parts = [
            f"{key}={sanitize_for_logging(value)}"
            for key, value in kwargs.items()
        ]
        parts.append(" ".join(metadata_parts))

    logger.info(" ".join(parts))
