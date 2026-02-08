"""Tests for core.metrics_logger module."""

import logging
from unittest.mock import MagicMock, patch

from atlas.core.metrics_logger import log_metric


def _make_settings(enabled: bool):
    settings = MagicMock()
    settings.feature_metrics_logging_enabled = enabled
    return settings


def _patch_config(enabled: bool):
    mock_cm = MagicMock()
    mock_cm.app_settings = _make_settings(enabled)
    return patch("atlas.modules.config.config_manager", mock_cm)


class TestLogMetric:
    def test_logs_when_enabled(self, caplog):
        with _patch_config(True):
            with caplog.at_level(logging.INFO, logger="atlas.core.metrics_logger"):
                log_metric("llm_call", "user@example.com", model="gpt-4", message_count=5)
        assert "[METRIC]" in caplog.text
        assert "[user@example.com]" in caplog.text
        assert "llm_call" in caplog.text
        assert "model=gpt-4" in caplog.text
        assert "message_count=5" in caplog.text

    def test_suppressed_when_disabled(self, caplog):
        with _patch_config(False):
            with caplog.at_level(logging.INFO, logger="atlas.core.metrics_logger"):
                log_metric("llm_call", "user@example.com", model="gpt-4")
        assert "[METRIC]" not in caplog.text

    def test_none_user_email_logs_unknown(self, caplog):
        with _patch_config(True):
            with caplog.at_level(logging.INFO, logger="atlas.core.metrics_logger"):
                log_metric("error", None, error_type="timeout")
        assert "[unknown]" in caplog.text
        assert "error_type=timeout" in caplog.text

    def test_integer_kwargs_handled(self, caplog):
        with _patch_config(True):
            with caplog.at_level(logging.INFO, logger="atlas.core.metrics_logger"):
                log_metric("file_upload", "u@test.com", file_size=1024, tool_count=0)
        assert "file_size=1024" in caplog.text
        assert "tool_count=0" in caplog.text

    def test_no_kwargs_produces_clean_output(self, caplog):
        with _patch_config(True):
            with caplog.at_level(logging.INFO, logger="atlas.core.metrics_logger"):
                log_metric("tool_call", "u@test.com")
        assert "[METRIC] [u@test.com] tool_call" in caplog.text
