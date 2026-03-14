"""Tests for Mandate 1: IdentifierSanitizer — strict identifier hygiene."""

import pytest
from semabridge.utils.identifiers import IdentifierSanitizer, SNOWFLAKE_RESERVED_WORDS, SQL_FUNCTION_NAMES


class TestIdentifierSanitizer:
    """Core sanitizer tests."""

    def setup_method(self):
        self.sanitizer = IdentifierSanitizer()

    # -- sanitize_column --------------------------------------------------

    def test_sanitize_column_basic(self):
        assert self.sanitizer.sanitize_column("Order Date") == "ORDER_DATE"

    def test_sanitize_column_strips_special_chars(self):
        assert self.sanitizer.sanitize_column("Sales (Net)") == "SALES_NET"

    def test_sanitize_column_uppercase(self):
        assert self.sanitizer.sanitize_column("revenue") == "REVENUE"

    def test_sanitize_column_leading_digit(self):
        result = self.sanitizer.sanitize_column("1stQuarter")
        # Sanitizer uppercases but does NOT prefix digits
        assert result == "1STQUARTER"

    def test_sanitize_column_empty(self):
        assert self.sanitizer.sanitize_column("") == "UNKNOWN"

    def test_sanitize_column_reserved_word(self):
        # suppress_reserved is True by default, but only applies to aliases
        result = self.sanitizer.sanitize_column("ORDER")
        assert result == "ORDER"

    def test_sanitize_column_reserved_word_suppressed(self):
        s = IdentifierSanitizer(suppress_reserved=True)
        assert s.sanitize_column("ORDER") == "ORDER"

    # -- sanitize_alias ---------------------------------------------------

    def test_sanitize_alias_basic(self):
        assert self.sanitizer.sanitize_alias("Customer Table") == "CUSTOMER_TABLE"

    def test_sanitize_alias_reserved(self):
        result = self.sanitizer.sanitize_alias("SELECT")
        assert result == "L_SELECT"

    # -- sanitize_table_name ----------------------------------------------

    def test_sanitize_table_name(self):
        assert self.sanitizer.sanitize_table_name("My Sales Table") == "MY_SALES_TABLE"

    def test_sanitize_table_name_reserved(self):
        # Table name sanitizer does NOT prefix reserved words
        result = self.sanitizer.sanitize_table_name("TABLE")
        assert result == "TABLE"

    # -- quote ------------------------------------------------------------

    def test_quote_basic(self):
        assert self.sanitizer.quote("REVENUE") == '"REVENUE"'

    def test_quote_already_quoted(self):
        assert self.sanitizer.quote('"REVENUE"') == '"REVENUE"'

    # -- sanitize_and_quote -----------------------------------------------

    def test_sanitize_and_quote(self):
        result = self.sanitizer.sanitize_and_quote("order date")
        assert result == '"ORDER_DATE"'

    # -- is_physical_source_column ----------------------------------------

    def test_physical_column_plain_ref(self):
        # A simple column name without brackets/operators is physical
        assert IdentifierSanitizer.is_physical_source_column('COLUMN_NAME') is True

    def test_physical_column_dax_ref(self):
        # DAX-style bracket references are NOT physical
        assert IdentifierSanitizer.is_physical_source_column('[TableName].[Column]') is False

    def test_physical_column_expression(self):
        assert IdentifierSanitizer.is_physical_source_column("DATEDIFF('day', A, B)") is False

    def test_physical_column_none(self):
        # None/empty means no computed expression → it IS a physical column
        assert IdentifierSanitizer.is_physical_source_column(None) is True

    # -- additional_reserved ----------------------------------------------

    def test_additional_reserved_words(self):
        s = IdentifierSanitizer(additional_reserved={"FOOBAR"})
        # additional_reserved affects alias suppression
        result = s.sanitize_alias("FOOBAR")
        assert result == "L_FOOBAR"

    # -- force_uppercase=False --------------------------------------------

    def test_no_force_uppercase(self):
        s = IdentifierSanitizer(force_uppercase=False)
        result = s.sanitize_column("myColumn")
        assert result == "myColumn"


class TestReservedWordSets:
    """Verify reserved word sets are populated."""

    def test_snowflake_reserved_not_empty(self):
        assert len(SNOWFLAKE_RESERVED_WORDS) > 100

    def test_sql_function_names_not_empty(self):
        assert len(SQL_FUNCTION_NAMES) > 50

    def test_common_reserved_present(self):
        for word in ["SELECT", "FROM", "WHERE", "ORDER", "GROUP", "TABLE"]:
            assert word in SNOWFLAKE_RESERVED_WORDS

    def test_common_functions_present(self):
        for func in ["SUM", "COUNT", "AVG", "MAX", "MIN", "COALESCE"]:
            assert func in SQL_FUNCTION_NAMES
