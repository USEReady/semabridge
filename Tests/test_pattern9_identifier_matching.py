import pytest
from semabridge.connectors.schema_compatibility_validator import SchemaCompatibilityValidator

def get_table_name_where_clause(raw_name: str) -> str:
    """Helper to generate correct SQL WHERE clause for table existence pre-flight checks."""
    if raw_name.startswith('"') and raw_name.endswith('"'):
        quoted_inner = raw_name[1:-1].replace("'", "''")
        return f"TABLE_NAME = '{quoted_inner}'"
    else:
        escaped = raw_name.upper().replace("'", "''")
        return f"TABLE_NAME = '{escaped}'"

def test_pattern9_quoted_vs_unquoted_identifier_matching():
    # Unquoted identifier should match exact uppercase TABLE_NAME
    clause_unquoted = get_table_name_where_clause("salesfact")
    assert clause_unquoted == "TABLE_NAME = 'SALESFACT'"

    # Quoted lowercase identifier must preserve exact case and not force UPPER()
    clause_quoted = get_table_name_where_clause('"salesfact"')
    assert clause_quoted == "TABLE_NAME = 'salesfact'"
    assert "UPPER(TABLE_NAME)" not in clause_quoted
