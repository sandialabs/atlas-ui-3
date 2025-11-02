from types import SimpleNamespace

import pytest

from core.utils import get_current_user, sanitize_for_logging


@pytest.mark.asyncio
async def test_get_current_user_default():
    class Dummy:
        pass
    req = SimpleNamespace(state=SimpleNamespace())
    assert await get_current_user(req) == "test@test.com"


@pytest.mark.asyncio
async def test_get_current_user_from_state():
    req = SimpleNamespace(state=SimpleNamespace(user_email="user@example.com"))
    assert await get_current_user(req) == "user@example.com"


class TestSanitizeForLogging:
    """Test suite for sanitize_for_logging function."""

    def test_clean_string_unchanged(self):
        """Test that strings without control characters are unchanged."""
        assert sanitize_for_logging("Hello World") == "Hello World"
        assert sanitize_for_logging("test123") == "test123"
        assert sanitize_for_logging("user@example.com") == "user@example.com"

    def test_removes_newlines(self):
        """Test that newlines are removed."""
        assert sanitize_for_logging("Hello\nWorld") == "HelloWorld"
        assert sanitize_for_logging("Line1\nLine2\nLine3") == "Line1Line2Line3"
        assert sanitize_for_logging("\nStarting newline") == "Starting newline"
        assert sanitize_for_logging("Trailing newline\n") == "Trailing newline"

    def test_removes_tabs(self):
        """Test that tabs are removed."""
        assert sanitize_for_logging("Hello\tWorld") == "HelloWorld"
        assert sanitize_for_logging("\tIndented") == "Indented"

    def test_removes_carriage_returns(self):
        """Test that carriage returns are removed."""
        assert sanitize_for_logging("Hello\rWorld") == "HelloWorld"
        assert sanitize_for_logging("Windows\r\nLine") == "WindowsLine"

    def test_removes_ansi_escape_sequences(self):
        """Test that ANSI escape sequences are removed."""
        # ANSI color codes
        assert sanitize_for_logging("\x1b[31mRed Text\x1b[0m") == "[31mRed Text[0m"
        assert sanitize_for_logging("\x1b[1;32mBold Green\x1b[0m") == "[1;32mBold Green[0m"

    def test_removes_null_bytes(self):
        """Test that null bytes are removed."""
        assert sanitize_for_logging("Hello\x00World") == "HelloWorld"
        assert sanitize_for_logging("\x00\x00test\x00") == "test"

    def test_removes_control_characters(self):
        """Test that various control characters are removed."""
        # Test C0 control characters (0x00-0x1f)
        assert sanitize_for_logging("Test\x01\x02\x03") == "Test"
        assert sanitize_for_logging("\x07Bell\x08Backspace") == "BellBackspace"

        # Test DEL character (0x7f)
        assert sanitize_for_logging("Test\x7fDEL") == "TestDEL"

        # Test C1 control characters (0x80-0x9f)
        assert sanitize_for_logging("Test\x80\x81\x9f") == "Test"

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert sanitize_for_logging("") == ""

    def test_none_value(self):
        """Test that None returns empty string."""
        assert sanitize_for_logging(None) == ""

    def test_integer_value(self):
        """Test that integers are converted to string."""
        assert sanitize_for_logging(123) == "123"
        assert sanitize_for_logging(0) == "0"
        assert sanitize_for_logging(-456) == "-456"

    def test_float_value(self):
        """Test that floats are converted to string."""
        assert sanitize_for_logging(3.14) == "3.14"
        assert sanitize_for_logging(-0.5) == "-0.5"

    def test_boolean_value(self):
        """Test that booleans are converted to string."""
        assert sanitize_for_logging(True) == "True"
        assert sanitize_for_logging(False) == "False"

    def test_list_value(self):
        """Test that lists are converted to string."""
        assert sanitize_for_logging([1, 2, 3]) == "[1, 2, 3]"
        assert sanitize_for_logging(["a", "b"]) == "['a', 'b']"

    def test_dict_value(self):
        """Test that dicts are converted to string."""
        result = sanitize_for_logging({"key": "value"})
        assert "key" in result and "value" in result

    def test_unicode_strings(self):
        """Test that unicode strings are handled correctly."""
        assert sanitize_for_logging("Hello 世界") == "Hello 世界"
        assert sanitize_for_logging("Café ☕") == "Café ☕"
        assert sanitize_for_logging("Test\n世界") == "Test世界"

    def test_mixed_control_characters(self):
        """Test strings with multiple types of control characters."""
        assert sanitize_for_logging("Line1\r\nLine2\tTab\x00Null") == "Line1Line2TabNull"
        assert sanitize_for_logging("\x01\x02Test\n\rData\x7f\x80") == "TestData"

    def test_log_injection_attempt(self):
        """Test that log injection attempts are sanitized."""
        # Simulate log injection attack
        malicious_input = "admin\n[INFO] Fake log entry\nAnother line"
        sanitized = sanitize_for_logging(malicious_input)
        assert "\n" not in sanitized
        assert sanitized == "admin[INFO] Fake log entryAnother line"

    def test_preserves_regular_punctuation(self):
        """Test that regular punctuation and symbols are preserved."""
        assert sanitize_for_logging("Hello, World!") == "Hello, World!"
        assert sanitize_for_logging("Cost: $100 (20% off)") == "Cost: $100 (20% off)"
        assert sanitize_for_logging("Email: test@example.com") == "Email: test@example.com"
