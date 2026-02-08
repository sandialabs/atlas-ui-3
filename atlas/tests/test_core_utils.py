from types import SimpleNamespace

import pytest

from atlas.core.log_sanitizer import get_current_user, sanitize_for_logging


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

    def test_removes_unicode_line_separator(self):
        """Test that Unicode LINE SEPARATOR (U+2028) is removed."""
        assert sanitize_for_logging("Hello\u2028World") == "HelloWorld"
        assert sanitize_for_logging("\u2028Starting") == "Starting"
        assert sanitize_for_logging("Ending\u2028") == "Ending"
        assert sanitize_for_logging("Line1\u2028Line2\u2028Line3") == "Line1Line2Line3"

    def test_removes_unicode_paragraph_separator(self):
        """Test that Unicode PARAGRAPH SEPARATOR (U+2029) is removed."""
        assert sanitize_for_logging("Para1\u2029Para2") == "Para1Para2"
        assert sanitize_for_logging("\u2029Starting") == "Starting"
        assert sanitize_for_logging("Ending\u2029") == "Ending"
        assert sanitize_for_logging("P1\u2029P2\u2029P3") == "P1P2P3"

    def test_removes_both_unicode_separators(self):
        """Test that both Unicode separators are removed together."""
        assert sanitize_for_logging("Text\u2028with\u2029separators") == "Textwithseparators"
        assert sanitize_for_logging("\u2028\u2029Mixed") == "Mixed"
        assert sanitize_for_logging("A\u2028B\u2029C\u2028D") == "ABCD"

    def test_unicode_separators_with_regular_unicode(self):
        """Test Unicode separators mixed with regular Unicode characters."""
        assert sanitize_for_logging("Hello\u2028世界") == "Hello世界"
        assert sanitize_for_logging("Café\u2029☕") == "Café☕"
        assert sanitize_for_logging("Test\u2028データ\u2029More") == "TestデータMore"

    def test_unicode_separator_log_injection(self):
        """Test that Unicode separators can't be used for log injection."""
        malicious_input = "user@example.com\u2028[ERROR] Fake error message\u2029[INFO] Fake info"
        sanitized = sanitize_for_logging(malicious_input)
        assert "\u2028" not in sanitized
        assert "\u2029" not in sanitized
        assert sanitized == "user@example.com[ERROR] Fake error message[INFO] Fake info"

    def test_multiple_consecutive_unicode_separators(self):
        """Test multiple consecutive Unicode separators are all removed."""
        assert sanitize_for_logging("Text\u2028\u2028\u2028More") == "TextMore"
        assert sanitize_for_logging("Text\u2029\u2029\u2029More") == "TextMore"
        assert sanitize_for_logging("\u2028\u2029\u2028\u2029Data") == "Data"

    def test_unicode_separators_with_ascii_control_chars(self):
        """Test Unicode separators combined with ASCII control characters."""
        assert sanitize_for_logging("Test\n\u2028\rData\u2029\tEnd") == "TestDataEnd"
        assert sanitize_for_logging("\x00\u2028Text\u2029\x1b[31m") == "Text[31m"

    def test_complex_unicode_injection_scenario(self):
        """Test complex scenario with Unicode separators in structured log attempt."""
        attack = "Normal text\u2028[2025-11-08 10:00:00] CRITICAL: Injected message\u2029admin logged in"
        sanitized = sanitize_for_logging(attack)
        assert "\u2028" not in sanitized
        assert "\u2029" not in sanitized
        assert "\n" not in sanitized
        assert sanitized == "Normal text[2025-11-08 10:00:00] CRITICAL: Injected messageadmin logged in"

    def test_unicode_separators_do_not_affect_other_unicode(self):
        """Test that removing Unicode separators doesn't affect other Unicode characters."""
        text_with_emoji = "Hello\u2028😀\u2029World🌍"
        assert sanitize_for_logging(text_with_emoji) == "Hello😀World🌍"

        text_with_chars = "Test\u2028中文\u2029العربية\u2028Ελληνικά"
        assert sanitize_for_logging(text_with_chars) == "Test中文العربيةΕλληνικά"
