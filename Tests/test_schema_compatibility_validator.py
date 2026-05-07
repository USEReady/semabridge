"""Unit tests for schema compatibility validator."""

import pytest
from unittest.mock import Mock, MagicMock, patch
from semabridge.connectors.schema_compatibility_validator import (
    SchemaCompatibilityValidator,
    SchemaCompatibilityResult,
    validate_table_exists_and_compatible
)


@pytest.fixture
def mock_cursor():
    """Create a mock Snowflake cursor."""
    return Mock()


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    config = Mock()
    config.schema_name = "PUBLIC"
    config.database = "SEMABRIDGE"
    return config


@pytest.fixture
def validator(mock_cursor, mock_config):
    """Create a validator instance with mocked dependencies."""
    return SchemaCompatibilityValidator(mock_cursor, mock_config)


class TestTableExists:
    """Tests for table existence checking."""
    
    def test_table_exists_returns_true_when_found(self, validator, mock_cursor):
        """Should return True when table exists in INFORMATION_SCHEMA."""
        mock_cursor.fetchone.return_value = [1]
        
        assert validator._table_exists("CUSTOMERS", "PUBLIC") is True
        
        # Verify query was executed
        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args[0][0]
        assert "INFORMATION_SCHEMA.TABLES" in call_args
        assert "CUSTOMERS" in call_args
    
    def test_table_exists_returns_false_when_not_found(self, validator, mock_cursor):
        """Should return False when table does not exist."""
        mock_cursor.fetchone.return_value = [0]
        
        assert validator._table_exists("NONEXISTENT", "PUBLIC") is False
    
    def test_table_exists_handles_case_insensitivity(self, validator, mock_cursor):
        """Should handle case-insensitive table names."""
        mock_cursor.fetchone.return_value = [1]
        
        validator._table_exists("customers", "public")
        
        call_args = mock_cursor.execute.call_args[0][0]
        # Query should uppercase the search terms
        assert "CUSTOMERS" in call_args
        assert "PUBLIC" in call_args
    
    def test_table_exists_handles_exception(self, validator, mock_cursor):
        """Should return False if query fails."""
        mock_cursor.execute.side_effect = Exception("Connection error")
        
        result = validator._table_exists("CUSTOMERS", "PUBLIC")
        assert result is False


class TestGetTableColumns:
    """Tests for retrieving table column metadata."""
    
    def test_get_table_columns_returns_columns_and_types(self, validator, mock_cursor):
        """Should return dict of column names to types."""
        mock_cursor.fetchall.return_value = [
            ("ID", "INTEGER"),
            ("NAME", "VARCHAR(500)"),
            ("CREATED_AT", "TIMESTAMP_NTZ")
        ]
        
        columns = validator._get_table_columns("CUSTOMERS", "PUBLIC")
        
        assert columns == {
            "ID": "INTEGER",
            "NAME": "VARCHAR(500)",
            "CREATED_AT": "TIMESTAMP_NTZ"
        }
    
    def test_get_table_columns_preserves_case(self, validator, mock_cursor):
        """Should preserve column name case."""
        mock_cursor.fetchall.return_value = [
            ("CustomerId", "INTEGER"),
            ("FirstName", "VARCHAR")
        ]
        
        columns = validator._get_table_columns("CUSTOMERS", "PUBLIC")
        
        assert "CustomerId" in columns
        assert "FirstName" in columns


class TestTypesCompatible:
    """Tests for data type compatibility checking."""
    
    @pytest.mark.parametrize("expected,actual,should_match", [
        # Exact matches
        ("INTEGER", "INTEGER", True),
        ("VARCHAR", "VARCHAR", True),
        ("DATE", "DATE", True),
        
        # Aliases
        ("INTEGER", "INT", True),
        ("DECIMAL", "NUMERIC", True),
        ("VARCHAR", "TEXT", True),
        ("FLOAT", "DOUBLE", True),
        
        # Numeric family (permissive)
        ("INTEGER", "DECIMAL", True),
        ("FLOAT", "INTEGER", True),
        
        # String family (permissive)
        ("VARCHAR", "VARCHAR(500)", True),
        ("TEXT", "VARCHAR(1000)", True),
        
        # Datetime family (permissive)
        ("TIMESTAMP", "TIMESTAMP_NTZ", True),
        ("TIMESTAMP_TZ", "TIMESTAMP_LTZ", True),
        
        # Incompatible
        ("INTEGER", "VARCHAR", False),
        ("DATE", "BOOLEAN", False),
        ("TIMESTAMP", "INTEGER", False),
    ])
    def test_types_compatible(self, expected, actual, should_match):
        """Should correctly identify compatible and incompatible types."""
        result = SchemaCompatibilityValidator._types_compatible(expected, actual)
        assert result == should_match
    
    def test_types_compatible_case_insensitive(self):
        """Should handle case-insensitive type names."""
        assert SchemaCompatibilityValidator._types_compatible("integer", "INTEGER") is True
        assert SchemaCompatibilityValidator._types_compatible("VARCHAR", "varchar") is True


class TestValidateTable:
    """Tests for full table validation."""
    
    def test_table_not_exists_returns_incompatible(self, validator, mock_cursor):
        """Should return incompatible result if table doesn't exist."""
        mock_cursor.fetchone.return_value = [0]  # Table not found
        
        result = validator.validate_table("MISSING_TABLE")
        
        assert result.is_compatible is False
        assert result.table_exists is False
    
    def test_compatible_table_with_all_required_columns(self, validator, mock_cursor):
        """Should return compatible when all required columns exist and types match."""
        # Mock table existence
        validator._table_exists = Mock(return_value=True)
        validator._get_table_columns = Mock(return_value={
            "CUSTOMER_ID": "INTEGER",
            "NAME": "VARCHAR(500)",
            "REVENUE": "DECIMAL(10,2)"
        })
        
        result = validator.validate_table(
            table_name="CUSTOMERS",
            join_columns={"CUSTOMER_ID", "NAME"},
            measure_columns={"REVENUE"},
            join_column_types={"CUSTOMER_ID": "INTEGER"}
        )
        
        assert result.is_compatible is True
        assert result.missing_join_columns == []
        assert result.missing_measure_columns == []
        assert result.type_mismatches == []
    
    def test_missing_join_column(self, validator, mock_cursor):
        """Should report missing join columns."""
        validator._table_exists = Mock(return_value=True)
        validator._get_table_columns = Mock(return_value={
            "NAME": "VARCHAR(500)"
        })
        
        result = validator.validate_table(
            table_name="CUSTOMERS",
            join_columns={"CUSTOMER_ID", "NAME"}
        )
        
        assert result.is_compatible is False
        assert "CUSTOMER_ID" in result.missing_join_columns
    
    def test_missing_measure_column(self, validator, mock_cursor):
        """Should report missing measure columns."""
        validator._table_exists = Mock(return_value=True)
        validator._get_table_columns = Mock(return_value={
            "CUSTOMER_ID": "INTEGER"
        })
        
        result = validator.validate_table(
            table_name="CUSTOMERS",
            measure_columns={"REVENUE", "PROFIT"}
        )
        
        assert result.is_compatible is False
        assert "REVENUE" in result.missing_measure_columns
        assert "PROFIT" in result.missing_measure_columns
    
    def test_type_mismatch_on_join_column(self, validator, mock_cursor):
        """Should report type mismatches on join columns."""
        validator._table_exists = Mock(return_value=True)
        validator._get_table_columns = Mock(return_value={
            "CUSTOMER_ID": "VARCHAR(50)"  # Wrong type!
        })
        
        result = validator.validate_table(
            table_name="CUSTOMERS",
            join_columns={"CUSTOMER_ID"},
            join_column_types={"CUSTOMER_ID": "INTEGER"}
        )
        
        assert result.is_compatible is False
        assert len(result.type_mismatches) > 0
        assert "CUSTOMER_ID" in result.type_mismatches[0]
    
    def test_extra_columns_warning(self, validator, mock_cursor):
        """Should warn (but not block) on extra columns."""
        validator._table_exists = Mock(return_value=True)
        validator._get_table_columns = Mock(return_value={
            "CUSTOMER_ID": "INTEGER",
            "NAME": "VARCHAR(500)",
            "LEGACY_FIELD": "VARCHAR(100)",  # Extra
            "OLD_CODE": "INTEGER"  # Extra
        })
        
        result = validator.validate_table(
            table_name="CUSTOMERS",
            join_columns={"CUSTOMER_ID", "NAME"}
        )
        
        assert result.is_compatible is True  # Still compatible
        assert "LEGACY_FIELD" in result.extra_columns
        assert "OLD_CODE" in result.extra_columns
        assert len(result.warnings) > 0
    
    def test_case_insensitive_column_matching(self, validator, mock_cursor):
        """Should match columns case-insensitively."""
        validator._table_exists = Mock(return_value=True)
        validator._get_table_columns = Mock(return_value={
            "CUSTOMER_ID": "INTEGER",
            "NAME": "VARCHAR(500)"
        })
        
        # Request with different case
        result = validator.validate_table(
            table_name="CUSTOMERS",
            join_columns={"customer_id", "name"}
        )
        
        assert result.is_compatible is True
        assert result.missing_join_columns == []


class TestErrorMessage:
    """Tests for error message generation."""
    
    def test_error_message_includes_all_issues(self):
        """Should include all types of issues in error message."""
        result = SchemaCompatibilityResult(
            is_compatible=False,
            table_exists=True,
            missing_join_columns=["CUSTOMER_ID"],
            missing_measure_columns=["REVENUE"],
            type_mismatches=["ORDER_DATE: expected DATE, got VARCHAR"],
            extra_columns=["LEGACY_FIELD"]
        )
        
        msg = result.error_message()
        
        assert "CUSTOMER_ID" in msg
        assert "REVENUE" in msg
        assert "ORDER_DATE" in msg
        assert "LEGACY_FIELD" in msg
    
    def test_error_message_formatting(self):
        """Should format error message readably."""
        result = SchemaCompatibilityResult(
            is_compatible=False,
            table_exists=True,
            missing_join_columns=["ID", "KEY"]
        )
        
        msg = result.error_message()
        
        assert "Schema compatibility issues found:" in msg
        assert "Missing join columns" in msg
        assert "ID" in msg and "KEY" in msg


class TestValidateTableExistsAndCompatible:
    """Tests for the convenience function."""
    
    def test_convenience_function_works(self):
        """Should wrap validator correctly."""
        mock_cursor = Mock()
        mock_config = Mock(schema_name="PUBLIC")
        
        with patch('semabridge.connectors.schema_compatibility_validator.SchemaCompatibilityValidator') as MockValidator:
            mock_validator = Mock()
            mock_validator.validate_table.return_value = SchemaCompatibilityResult(
                is_compatible=True,
                table_exists=True
            )
            MockValidator.return_value = mock_validator
            
            result = validate_table_exists_and_compatible(
                mock_cursor,
                "CUSTOMERS",
                "PUBLIC",
                mock_config,
                join_columns={"ID"}
            )
            
            assert result.is_compatible is True
            mock_validator.validate_table.assert_called_once()
