"""
Tests for Logging Initialization.

Tests logging level precedence and format handling.
"""

import logging
import pytest

from semabridge.utils.logging_init import (
    initialize_logging,
    setup_logging_with_config,
    reset_logging,
    is_logging_initialized,
    JsonFormatter,
)


@pytest.fixture(autouse=True)
def cleanup_logging():
    """Reset logging state before and after each test."""
    reset_logging()
    yield
    reset_logging()


class TestLoggingPrecedence:
    """Tests for logging level precedence."""
    
    def test_cli_log_level_highest_precedence(self):
        """CLI --log-level takes precedence over everything."""
        level = initialize_logging(
            cli_log_level="DEBUG",
            yaml_log_level="WARNING",
            verbose=False,
        )
        assert level == "DEBUG"
    
    def test_verbose_flag_over_yaml(self):
        """--verbose flag takes precedence over YAML config."""
        level = initialize_logging(
            cli_log_level=None,
            yaml_log_level="WARNING",
            verbose=True,
        )
        assert level == "DEBUG"
    
    def test_yaml_log_level_used(self):
        """YAML logging.level used when no CLI override."""
        level = initialize_logging(
            cli_log_level=None,
            yaml_log_level="WARNING",
            verbose=False,
        )
        assert level == "WARNING"
    
    def test_default_level(self):
        """Default INFO used when nothing specified."""
        level = initialize_logging(
            cli_log_level=None,
            yaml_log_level=None,
            verbose=False,
        )
        assert level == "INFO"
    
    def test_invalid_level_falls_back_to_info(self):
        """Invalid log level falls back to INFO."""
        level = initialize_logging(
            cli_log_level="INVALID_LEVEL",
            yaml_log_level=None,
            verbose=False,
        )
        assert level == "INFO"
    
    def test_case_insensitive(self):
        """Log level is case-insensitive."""
        level = initialize_logging(
            cli_log_level="debug",
            yaml_log_level=None,
            verbose=False,
        )
        assert level == "DEBUG"


class TestLoggingFormat:
    """Tests for logging format handling."""
    
    def test_text_format_default(self):
        """Text format is used by default."""
        initialize_logging(yaml_log_format=None)
        # Test would verify handler type, but we mainly ensure no errors
    
    def test_json_format(self):
        """JSON format is set when specified."""
        initialize_logging(yaml_log_format="json")
        # Verify logging initialized without error


class TestJsonFormatter:
    """Tests for JSON log formatter."""
    
    def test_json_output_structure(self):
        """Test that JSON formatter produces valid structure."""
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        
        import json
        parsed = json.loads(output)
        
        assert "timestamp" in parsed
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test.logger"
        assert parsed["message"] == "Test message"
    
    def test_json_with_exception(self):
        """Test JSON formatter with exception info."""
        formatter = JsonFormatter()
        
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
        
        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        output = formatter.format(record)
        
        import json
        parsed = json.loads(output)
        
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]


class TestLoggingState:
    """Tests for logging initialization state."""
    
    def test_not_initialized_initially(self):
        """Logging is not initialized after reset."""
        reset_logging()
        assert not is_logging_initialized()
    
    def test_initialized_after_setup(self):
        """Logging is initialized after setup."""
        initialize_logging()
        assert is_logging_initialized()
    
    def test_reset_clears_state(self):
        """Reset clears initialization state."""
        initialize_logging()
        reset_logging()
        assert not is_logging_initialized()


class TestSetupLoggingWithConfig:
    """Tests for direct logging setup."""
    
    def test_setup_with_debug_level(self):
        """Setup with DEBUG level."""
        setup_logging_with_config(level="DEBUG")
        logger = logging.getLogger("test")
        assert logger.getEffectiveLevel() <= logging.DEBUG or logging.getLogger().level <= logging.DEBUG
    
    def test_setup_with_json_format(self):
        """Setup with JSON format."""
        setup_logging_with_config(level="INFO", format_type="json")
        # Verify no errors
    
    def test_setup_with_text_format(self):
        """Setup with text format."""
        setup_logging_with_config(level="INFO", format_type="text", rich_output=False)
        # Verify no errors
