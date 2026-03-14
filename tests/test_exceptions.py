"""
Unit tests for semabridge.core.exceptions.

Tests the custom exception hierarchy and their behavior.
"""

import pytest

from semabridge.core.exceptions import (
    SemaBridgeError,
    ConnectorError,
    MissingCredentialError,
    ConversionError,
    ValidationError,
    RepositoryError,
    PluginError,
)


class TestSemaBridgeError:
    """Test base SemaBridgeError class."""

    def test_basic_message(self):
        """Test basic error message."""
        error = SemaBridgeError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.message == "Something went wrong"

    def test_with_details(self):
        """Test error with additional details."""
        error = SemaBridgeError("Error occurred", details={"code": 123, "type": "test"})
        assert "code=123" in str(error)
        assert "type=test" in str(error)
        assert error.details == {"code": 123, "type": "test"}

    def test_empty_details(self):
        """Test error with no details shows only message."""
        error = SemaBridgeError("Simple error")
        assert str(error) == "Simple error"
        assert error.details == {}


class TestConnectorError:
    """Test ConnectorError class."""

    def test_basic_connector_error(self):
        """Test basic connector error."""
        error = ConnectorError("Connection failed")
        assert "Connection failed" in str(error)
        assert error.connector_name is None

    def test_with_connector_name(self):
        """Test error with connector name."""
        error = ConnectorError("Auth failed", connector_name="snowflake")
        assert "Auth failed" in str(error)
        assert "connector=snowflake" in str(error)
        assert error.connector_name == "snowflake"

    def test_with_additional_details(self):
        """Test error with connector and additional details."""
        error = ConnectorError(
            "Connection timeout",
            connector_name="fabric",
            details={"timeout_ms": 5000}
        )
        assert "connector=fabric" in str(error)
        assert "timeout_ms=5000" in str(error)


class TestMissingCredentialError:
    """Test MissingCredentialError class."""

    def test_basic_missing_credential(self):
        """Test basic missing credential error."""
        error = MissingCredentialError("SNOWFLAKE_PASSWORD")
        assert "SNOWFLAKE_PASSWORD" in str(error)
        assert "is not set" in str(error)
        assert error.env_var_name == "SNOWFLAKE_PASSWORD"

    def test_with_connector_name(self):
        """Test missing credential with connector context."""
        error = MissingCredentialError(
            "FABRIC_TOKEN",
            connector_name="FabricConnector"
        )
        assert "FABRIC_TOKEN" in str(error)
        assert "connector=FabricConnector" in str(error)
        assert error.connector_name == "FabricConnector"

    def test_inheritance(self):
        """Test that MissingCredentialError is a ConnectorError."""
        error = MissingCredentialError("SECRET_KEY")
        assert isinstance(error, ConnectorError)
        assert isinstance(error, SemaBridgeError)


class TestConversionError:
    """Test ConversionError class."""

    def test_basic_conversion_error(self):
        """Test basic conversion error."""
        error = ConversionError("Cannot convert model")
        assert "Cannot convert model" in str(error)

    def test_with_formats(self):
        """Test conversion error with source/target formats."""
        error = ConversionError(
            "Incompatible schema",
            source_format="TMSL",
            target_format="SML"
        )
        assert "source_format=TMSL" in str(error)
        assert "target_format=SML" in str(error)
        assert error.source_format == "TMSL"
        assert error.target_format == "SML"


class TestValidationError:
    """Test ValidationError class."""

    def test_basic_validation_error(self):
        """Test basic validation error."""
        error = ValidationError("Invalid model")
        assert "Invalid model" in str(error)

    def test_with_field(self):
        """Test validation error with field info."""
        error = ValidationError("Required field missing", field="unique_name")
        assert "field=unique_name" in str(error)
        assert error.field == "unique_name"

    def test_with_line_number(self):
        """Test validation error with YAML line number."""
        error = ValidationError(
            "Syntax error",
            field="metrics",
            line_number=42
        )
        assert "line=42" in str(error)
        assert error.line_number == 42

    def test_with_error_list(self):
        """Test validation error with multiple errors."""
        error = ValidationError(
            "Multiple validation errors",
            errors=["Error 1", "Error 2", "Error 3"]
        )
        assert "error_count=3" in str(error)
        assert error.errors == ["Error 1", "Error 2", "Error 3"]


class TestRepositoryError:
    """Test RepositoryError class."""

    def test_basic_repo_error(self):
        """Test basic repository error."""
        error = RepositoryError("Database corrupted")
        assert "Database corrupted" in str(error)

    def test_with_operation(self):
        """Test repository error with operation type."""
        error = RepositoryError("Failed to commit", operation="version_save")
        assert "operation=version_save" in str(error)
        assert error.operation == "version_save"


class TestPluginError:
    """Test PluginError class."""

    def test_basic_plugin_error(self):
        """Test basic plugin error."""
        error = PluginError("Plugin crashed")
        assert "Plugin crashed" in str(error)

    def test_with_plugin_name(self):
        """Test plugin error with plugin name."""
        error = PluginError("Init failed", plugin_name="custom-connector")
        assert "plugin=custom-connector" in str(error)
        assert error.plugin_name == "custom-connector"


class TestExceptionHierarchy:
    """Test exception inheritance hierarchy."""

    def test_all_inherit_from_base(self):
        """Test all exceptions inherit from SemaBridgeError."""
        exceptions = [
            ConnectorError("test"),
            MissingCredentialError("VAR"),
            ConversionError("test"),
            ValidationError("test"),
            RepositoryError("test"),
            PluginError("test"),
        ]
        for exc in exceptions:
            assert isinstance(exc, SemaBridgeError)

    def test_catching_base_catches_all(self):
        """Test catching SemaBridgeError catches all child exceptions."""
        with pytest.raises(SemaBridgeError):
            raise ConnectorError("test")

        with pytest.raises(SemaBridgeError):
            raise ValidationError("test")

        with pytest.raises(SemaBridgeError):
            raise PluginError("test")

    def test_missing_credential_is_connector_error(self):
        """Test MissingCredentialError can be caught as ConnectorError."""
        with pytest.raises(ConnectorError):
            raise MissingCredentialError("VAR")
