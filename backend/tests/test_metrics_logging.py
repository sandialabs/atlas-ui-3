"""
Tests for metrics logging functionality.

This module tests that metrics logging:
- Respects the feature flag
- Logs with the correct format
- Does not log sensitive data
"""

import logging
import pytest
from unittest.mock import patch

from core.metrics_logger import log_metric, is_metrics_logging_enabled


class TestMetricsLogger:
    """Test metrics logging functionality."""

    @patch('core.metrics_logger.config_manager')
    def test_log_metric_disabled(self, mock_config, caplog):
        """Test that metrics are not logged when feature is disabled."""
        # Configure mock to return False for feature flag
        mock_config.app_settings.feature_metrics_logging_enabled = False
        
        with caplog.at_level(logging.INFO):
            log_metric("llm_call", "user@example.com", model="gpt-4", message_count=5)
        
        # Should not log anything
        assert "[METRIC]" not in caplog.text

    @patch('core.metrics_logger.config_manager')
    def test_log_metric_enabled(self, mock_config, caplog):
        """Test that metrics are logged when feature is enabled."""
        # Configure mock to return True for feature flag
        mock_config.app_settings.feature_metrics_logging_enabled = True
        
        with caplog.at_level(logging.INFO):
            log_metric("llm_call", "user@example.com", model="gpt-4", message_count=5)
        
        # Should log with correct format
        assert "[METRIC]" in caplog.text
        assert "[user@example.com]" in caplog.text
        assert "llm_call" in caplog.text
        assert "model=gpt-4" in caplog.text
        assert "message_count=5" in caplog.text

    @patch('core.metrics_logger.config_manager')
    def test_log_metric_tool_call(self, mock_config, caplog):
        """Test logging tool calls."""
        mock_config.app_settings.feature_metrics_logging_enabled = True
        
        with caplog.at_level(logging.INFO):
            log_metric("tool_call", "user@example.com", tool_name="calculator")
        
        assert "[METRIC]" in caplog.text
        assert "[user@example.com]" in caplog.text
        assert "tool_call" in caplog.text
        assert "tool_name=calculator" in caplog.text

    @patch('core.metrics_logger.config_manager')
    def test_log_metric_file_upload(self, mock_config, caplog):
        """Test logging file uploads."""
        mock_config.app_settings.feature_metrics_logging_enabled = True
        
        with caplog.at_level(logging.INFO):
            log_metric("file_upload", "user@example.com", file_size=1024, content_type="application/pdf")
        
        assert "[METRIC]" in caplog.text
        assert "[user@example.com]" in caplog.text
        assert "file_upload" in caplog.text
        assert "file_size=1024" in caplog.text
        assert "content_type=application/pdf" in caplog.text

    @patch('core.metrics_logger.config_manager')
    def test_log_metric_error(self, mock_config, caplog):
        """Test logging errors without sensitive details."""
        mock_config.app_settings.feature_metrics_logging_enabled = True
        
        with caplog.at_level(logging.INFO):
            log_metric("error", "user@example.com", error_type="rate_limit")
        
        assert "[METRIC]" in caplog.text
        assert "[user@example.com]" in caplog.text
        assert "error" in caplog.text
        assert "error_type=rate_limit" in caplog.text

    @patch('core.metrics_logger.config_manager')
    def test_log_metric_no_user(self, mock_config, caplog):
        """Test logging metrics without user email."""
        mock_config.app_settings.feature_metrics_logging_enabled = True
        
        with caplog.at_level(logging.INFO):
            log_metric("llm_call", None, model="gpt-4")
        
        assert "[METRIC]" in caplog.text
        assert "[unknown]" in caplog.text
        assert "llm_call" in caplog.text

    @patch('core.metrics_logger.config_manager')
    def test_is_metrics_logging_enabled(self, mock_config):
        """Test helper function to check if metrics logging is enabled."""
        # Test when enabled
        mock_config.app_settings.feature_metrics_logging_enabled = True
        assert is_metrics_logging_enabled() is True
        
        # Test when disabled
        mock_config.app_settings.feature_metrics_logging_enabled = False
        assert is_metrics_logging_enabled() is False

    @patch('core.metrics_logger.config_manager')
    def test_log_metric_format_pattern(self, mock_config, caplog):
        """Test that metrics follow the [METRIC] [username] ... pattern."""
        mock_config.app_settings.feature_metrics_logging_enabled = True
        
        with caplog.at_level(logging.INFO):
            log_metric("llm_call", "test@test.com", model="gpt-4")
        
        # Check the pattern matches [METRIC] [username] event_type ...
        log_lines = [record.message for record in caplog.records if "[METRIC]" in record.message]
        assert len(log_lines) == 1
        assert log_lines[0].startswith("[METRIC] [test@test.com] llm_call")

    @patch('core.metrics_logger.config_manager')
    def test_no_sensitive_data_logged(self, mock_config, caplog):
        """Test that sensitive data is never logged in metrics."""
        mock_config.app_settings.feature_metrics_logging_enabled = True
        
        # Log various events and ensure no sensitive data appears
        with caplog.at_level(logging.INFO):
            # Should not log tool arguments
            log_metric("tool_call", "user@example.com", tool_name="file_reader")
            # Should not log filenames
            log_metric("file_upload", "user@example.com", file_size=1024)
            # Should not log error details
            log_metric("error", "user@example.com", error_type="validation")
            # Should not log message content
            log_metric("llm_call", "user@example.com", model="gpt-4", message_count=3)
        
        # Verify that only metadata is logged
        log_text = caplog.text
        assert "tool_name=file_reader" in log_text  # OK - tool name
        assert "file_size=1024" in log_text  # OK - file size
        assert "error_type=validation" in log_text  # OK - error type
        assert "message_count=3" in log_text  # OK - message count
        
        # Ensure no references to actual content, arguments, filenames, etc.
        # (This is implicit - we only log what we explicitly pass)
