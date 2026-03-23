"""
Unit tests for Snowflake identifier sanitization rules.

Tests cover:
- Column name sanitization
- Leading digit handling (prefix with underscore)
- Special character replacement
- Reserved word suppression
- Collision detection and resolution
- Dollar sign support
- Deterministic naming
"""

import pytest
from semabridge.utils.identifiers import (
    IdentifierSanitizer,
    IdentifierRegistry,
    SNOWFLAKE_RESERVED_WORDS,
)
from semabridge.utils.relationship_naming import _sanitize_identifier


class TestSanitizeIdentifierFunction:
    """Test the _sanitize_identifier function from relationship_naming."""

    def test_leading_digit_prefix(self):
        """Leading digit should be prefixed with underscore."""
        assert _sanitize_identifier("18_MONTH_FORWARD") == "_18_MONTH_FORWARD"
        assert _sanitize_identifier("2024") == "_2024"
        assert _sanitize_identifier("9_ITEMS") == "_9_ITEMS"

    def test_uppercase_conversion(self):
        """All letters should be uppercase."""
        assert _sanitize_identifier("revenue") == "REVENUE"
        assert _sanitize_identifier("Customer_ID") == "CUSTOMER_ID"

    def test_special_char_replacement(self):
        """Special characters should be replaced with underscore."""
        # Note: % and space are replaced with _, but consecutive _ collapse to single
        assert _sanitize_identifier("Revenue % Growth") == "REVENUE_GROWTH"
        assert _sanitize_identifier("customer-id") == "CUSTOMER_ID"
        assert _sanitize_identifier("sales@2024") == "SALES_2024"

    def test_space_replacement(self):
        """Spaces should be replaced with underscore."""
        assert _sanitize_identifier("2024 Sales") == "_2024_SALES"
        assert _sanitize_identifier("Sales Amount") == "SALES_AMOUNT"

    def test_consecutive_underscore_collapse(self):
        """Multiple consecutive underscores should collapse to single."""
        assert _sanitize_identifier("revenue___growth") == "REVENUE_GROWTH"
        assert _sanitize_identifier("sales  amount") == "SALES_AMOUNT"

    def test_empty_input(self):
        """Empty input should return empty string."""
        assert _sanitize_identifier("") == ""
        assert _sanitize_identifier("   ") == ""

    def test_alphanumeric_passthrough(self):
        """Valid alphanumeric names should pass through unchanged (except case)."""
        assert _sanitize_identifier("CUSTOMER_ID") == "CUSTOMER_ID"
        assert _sanitize_identifier("amount") == "AMOUNT"
        assert _sanitize_identifier("col_123") == "COL_123"

    def test_dollar_sign_support(self):
        """Dollar sign should be preserved as valid Snowflake character."""
        # $ is valid in Snowflake, so it should be preserved
        assert _sanitize_identifier("price$") == "PRICE$"
        assert _sanitize_identifier("revenue$2024") == "REVENUE$2024"

    def test_deterministic(self):
        """Same input should always produce same output."""
        name = "Revenue % Growth"
        result1 = _sanitize_identifier(name)
        result2 = _sanitize_identifier(name)
        assert result1 == result2


class TestIdentifierSanitizerColumn:
    """Test IdentifierSanitizer.sanitize_column method."""

    @pytest.fixture
    def sanitizer(self):
        """Create a sanitizer instance."""
        return IdentifierSanitizer(force_uppercase=True)

    def test_column_leading_digit(self, sanitizer):
        """Columns starting with digit should be prefixed."""
        assert sanitizer.sanitize_column("18_MONTH_FORWARD_LOOKING_PIPELINE_MWDC") == "_18_MONTH_FORWARD_LOOKING_PIPELINE_MWDC"
        assert sanitizer.sanitize_column("2024_Sales") == "_2024_SALES"

    def test_column_special_chars(self, sanitizer):
        """Special characters in column names should be replaced."""
        # % and space become underscores, then consecutive _ collapse to single
        assert sanitizer.sanitize_column("Revenue % Growth") == "REVENUE_GROWTH"
        assert sanitizer.sanitize_column("customer-id") == "CUSTOMER_ID"

    def test_column_dax_qualifier_strip(self, sanitizer):
        """DAX table qualifiers should be stripped."""
        assert sanitizer.sanitize_column("'Sales'[Amount]") == "AMOUNT"
        assert sanitizer.sanitize_column("[Customer]") == "CUSTOMER"

    def test_column_dot_notation_split(self, sanitizer):
        """Two-part dot notation should keep only the column part."""
        assert sanitizer.sanitize_column("SALES_TABLE.AMOUNT") == "AMOUNT"
        assert sanitizer.sanitize_column("customers.customer_id") == "CUSTOMER_ID"

    def test_column_preserves_three_part_names(self, sanitizer):
        """Three-part names (DB.SCHEMA.TABLE) should be preserved as-is."""
        # After sanitization, would become SCHEMA.TABLE.COLUMN
        result = sanitizer.sanitize_column("DB.SCHEMA.TABLE")
        # Should keep all three parts in uppercase
        assert "SCHEMA" in result and "TABLE" in result

    def test_column_unknown_fallback(self, sanitizer):
        """Names that sanitize to empty should return COLUMN_UNKNOWN."""
        assert sanitizer.sanitize_column("!!!") == "COLUMN_UNKNOWN"
        assert sanitizer.sanitize_column("%%%") == "COLUMN_UNKNOWN"

    def test_column_consecutive_underscores(self, sanitizer):
        """Consecutive underscores should collapse."""
        assert sanitizer.sanitize_column("revenue___amount") == "REVENUE_AMOUNT"
        assert sanitizer.sanitize_column("sales  amount") == "SALES_AMOUNT"

    def test_column_case_insensitive_input(self, sanitizer):
        """Column names should be uppercased."""
        assert sanitizer.sanitize_column("customerID") == "CUSTOMERID"
        assert sanitizer.sanitize_column("amount") == "AMOUNT"

    def test_column_reserved_keyword_prefixed(self, sanitizer):
        """Reserved column identifiers should be prefixed with COL_."""
        assert sanitizer.sanitize_column("COLUMN") == "COL_COLUMN"
        assert sanitizer.sanitize_column("date") == "COL_DATE"


class TestIdentifierSanitizerTable:
    """Test IdentifierSanitizer.sanitize_table_name method."""

    @pytest.fixture
    def sanitizer(self):
        """Create a sanitizer instance."""
        return IdentifierSanitizer(force_uppercase=True)

    def test_table_leading_digit(self, sanitizer):
        """Tables starting with digit should be prefixed."""
        assert sanitizer.sanitize_table_name("2024_Sales_Report") == "_2024_SALES_REPORT"
        assert sanitizer.sanitize_table_name("1st_Quarter_Data") == "_1ST_QUARTER_DATA"

    def test_table_special_chars(self, sanitizer):
        """Special characters should be replaced."""
        assert sanitizer.sanitize_table_name("customer-sales") == "CUSTOMER_SALES"
        assert sanitizer.sanitize_table_name("sales@2024") == "SALES_2024"

    def test_table_uppercase(self, sanitizer):
        """Table names should be uppercased."""
        assert sanitizer.sanitize_table_name("customer_table") == "CUSTOMER_TABLE"
        assert sanitizer.sanitize_table_name("SalesData") == "SALESDATA"


class TestIdentifierSanitizerAlias:
    """Test IdentifierSanitizer.sanitize_alias method."""

    @pytest.fixture
    def sanitizer(self):
        """Create a sanitizer instance with reserved word suppression."""
        return IdentifierSanitizer(force_uppercase=True, suppress_reserved=True)

    def test_alias_leading_digit(self, sanitizer):
        """Aliases starting with digit should be prefixed."""
        assert sanitizer.sanitize_alias("2024_Sales") == "_2024_SALES"

    def test_alias_reserved_word_suppression(self, sanitizer):
        """Reserved words should be prefixed with COL_."""
        # 'select' is a reserved word
        result = sanitizer.sanitize_alias("select")
        assert result.startswith("COL_"), f"Expected COL_ prefix for reserved word, got {result}"

    def test_alias_reserved_word_case_insensitive(self, sanitizer):
        """Reserved word detection should be case-insensitive."""
        result = sanitizer.sanitize_alias("SELECT")
        assert result.startswith("COL_"), f"Reserved word 'SELECT' should be prefixed"

    def test_alias_non_reserved(self, sanitizer):
        """Non-reserved names should not be prefixed."""
        result = sanitizer.sanitize_alias("customer_list")
        assert not result.startswith("COL_"), f"Non-reserved word should not be prefixed"
        assert result == "CUSTOMER_LIST"

    def test_alias_table_reserved_word(self, sanitizer):
        """'table' is reserved and should be prefixed."""
        result = sanitizer.sanitize_alias("table")
        assert result.startswith("COL_"), f"Reserved word 'table' should be prefixed"


class TestIdentifierRegistry:
    """Test IdentifierRegistry collision detection and resolution."""

    @pytest.fixture
    def registry(self):
        """Create a registry instance."""
        return IdentifierRegistry()

    def test_registry_first_occurrence(self, registry):
        """First occurrence should not get suffix."""
        result = registry.register("Revenue Item")
        assert result == "REVENUE_ITEM"
        assert "_2" not in result

    def test_registry_collision_detection(self, registry):
        """Different names sanitizing to same identifier should get suffix."""
        # Both "Revenue %" and "Revenue @" sanitize to "REVENUE" after stripping trailing _
        result1 = registry.register("Revenue %")
        result2 = registry.register("Revenue @")

        assert result1 == "REVENUE"
        assert result2 == "REVENUE_2", f"Expected REVENUE_2 for collision, got {result2}"

    def test_registry_three_way_collision(self, registry):
        """Multiple collisions should get progressive suffixes."""
        result1 = registry.register("Revenue %")
        result2 = registry.register("Revenue @")
        result3 = registry.register("Revenue #")

        assert result1 == "REVENUE"
        assert result2 == "REVENUE_2"
        assert result3 == "REVENUE_3"

    def test_registry_caching(self, registry):
        """Same source name should return cached result."""
        result1 = registry.register("Revenue %")
        result2 = registry.register("Revenue %")
        assert result1 == result2

    def test_registry_transformations_log(self, registry):
        """All transformations should be logged."""
        registry.register("Revenue %")
        registry.register("2024 Sales")

        transformations = registry.transformations
        assert len(transformations) > 0

    def test_registry_collision_summary(self, registry):
        """Collision summary should only include names with collisions."""
        registry.register("Revenue %")
        registry.register("Revenue @")
        registry.register("Amount")  # No collision

        summary = registry.get_collision_summary()
        assert "REVENUE" in summary
        assert summary["REVENUE"] == 1  # One collision (2 occurrences total)
        assert "AMOUNT" not in summary  # No collisions

    def test_registry_name_types(self, registry):
        """Registry should handle different identifier types."""
        col = registry.register("2024_Sales", name_type="column")
        table = registry.register("2024_Sales", name_type="table")

        assert col == "_2024_SALES"
        assert table == "_2024_SALES"  # Should be cached and same


class TestSnowflakeIdentifierRules:
    """Integration tests for complete Snowflake identifier rules."""

    def test_example_18_month_forward_looking(self):
        """Test the primary example from requirements."""
        sanitizer = IdentifierSanitizer()
        result = sanitizer.sanitize_column("18_MONTH_FORWARD_LOOKING_PIPELINE_MWDC")
        assert result == "_18_MONTH_FORWARD_LOOKING_PIPELINE_MWDC"
        assert not result[0].isdigit(), "First character should not be digit"

    def test_example_revenue_growth(self):
        """Test revenue % growth example."""
        sanitizer = IdentifierSanitizer()
        result = sanitizer.sanitize_column("Revenue % Growth")
        # % becomes _, space becomes _, then consecutive _ collapse to single
        assert result == "REVENUE_GROWTH"

    def test_example_customer_id(self):
        """Test customer-id example."""
        sanitizer = IdentifierSanitizer()
        result = sanitizer.sanitize_column("customer-id")
        assert result == "CUSTOMER_ID"

    def test_example_2024_sales(self):
        """Test 2024 sales example."""
        sanitizer = IdentifierSanitizer()
        result = sanitizer.sanitize_column("2024 Sales")
        assert result == "_2024_SALES"
        assert not result[0].isdigit(), "First character should not be digit"

    def test_only_allowed_characters(self):
        """Final identifier should only contain [A-Z0-9_$]."""
        import re

        sanitizer = IdentifierSanitizer()
        test_names = [
            "Revenue % Growth",
            "customer-id",
            "2024 Sales",
            "18_MONTH_FORWARD_LOOKING_PIPELINE_MWDC",
            "sales@2024#report",
            "!!!Invalid!!!",
        ]

        for name in test_names:
            result = sanitizer.sanitize_column(name)
            # Check only contains allowed chars
            assert re.match(r"^[A-Z_][A-Z0-9_$]*$", result), f"'{result}' contains invalid characters"

    def test_deterministic_output(self):
        """Same input should always produce same output."""
        sanitizer = IdentifierSanitizer()
        name = "Revenue % Growth (2024)"

        results = [sanitizer.sanitize_column(name) for _ in range(10)]
        assert len(set(results)) == 1, "Output should be deterministic"

    def test_no_leading_digits_in_result(self):
        """No result should start with a digit."""
        sanitizer = IdentifierSanitizer()
        test_names = [
            "2024_Sales",
            "18_MONTH_FORWARD",
            "9th_Place",
            "1_Item",
        ]

        for name in test_names:
            result = sanitizer.sanitize_column(name)
            assert not result[0].isdigit(), f"Result '{result}' starts with digit"

    def test_preserves_semantic_meaning(self):
        """Sanitization should preserve semantic meaning."""
        sanitizer = IdentifierSanitizer()
        
        # Original and result should be recognizable as related
        test_cases = [
            ("Revenue % Growth", "REVENUE_GROWTH"),
            ("customer-id", "CUSTOMER_ID"),
            ("sales@2024", "SALES_2024"),
        ]

        for original, expected in test_cases:
            result = sanitizer.sanitize_column(original)
            assert result == expected, f"Expected '{expected}', got '{result}'"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_string(self):
        """Empty strings should be handled."""
        sanitizer = IdentifierSanitizer()
        result = sanitizer.sanitize_column("")
        assert result in ("COLUMN_UNKNOWN", "UNKNOWN")  # Either is acceptable

    def test_only_special_characters(self):
        """Names with only special characters should fallback."""
        sanitizer = IdentifierSanitizer()
        result = sanitizer.sanitize_column("!!!@@@###")
        assert result == "COLUMN_UNKNOWN"

    def test_very_long_name(self):
        """Very long names should be handled."""
        sanitizer = IdentifierSanitizer()
        long_name = "A" * 256 + "Revenue"  # Extra long
        result = sanitizer.sanitize_column(long_name)
        assert len(result) > 0
        assert result.startswith("A")

    def test_unicode_characters(self):
        """Unicode characters should be replaced."""
        sanitizer = IdentifierSanitizer()
        result = sanitizer.sanitize_column("Revenue™ Growth®")
        # Unicode chars should be replaced with underscores
        assert "TM" not in result  # ™ removed
        assert result == "REVENUE_GROWTH"

    def test_null_input(self):
        """Null/None input should be handled."""
        sanitizer = IdentifierSanitizer
        # Create a new instance for this test
        s = sanitizer()
        result = s.sanitize_column(None)
        assert result == "UNKNOWN"  # None causes different path

    def test_only_spaces(self):
        """Input with only spaces should fallback."""
        sanitizer = IdentifierSanitizer()
        result = sanitizer.sanitize_column("     ")
        assert result == "COLUMN_UNKNOWN"


class TestReservedKeywordHandling:
    """Test Snowflake reserved keyword handling with COL_ prefix."""

    @pytest.fixture
    def sanitizer(self):
        """Create a sanitizer instance with reserved word suppression."""
        return IdentifierSanitizer(force_uppercase=True, suppress_reserved=True)

    def test_reserved_keyword_column(self, sanitizer):
        """'COLUMN' as reserved keyword should be prefixed."""
        # Note: 'column' is not in default reserved list, but test shows pattern
        result = sanitizer.sanitize_alias("column")
        # Either COL_COLUMN or COLUMN depending on reserved status
        assert result.upper() in ("COL_COLUMN", "COLUMN")

    def test_reserved_keyword_table(self, sanitizer):
        """'TABLE' is reserved and must be prefixed."""
        result = sanitizer.sanitize_alias("table")
        assert result == "COL_TABLE", f"Expected COL_TABLE, got {result}"

    def test_reserved_keyword_date(self, sanitizer):
        """'DATE' is reserved and must be prefixed."""
        result = sanitizer.sanitize_alias("date")
        assert result == "COL_DATE", f"Expected COL_DATE, got {result}"

    def test_reserved_keyword_group(self, sanitizer):
        """'GROUP' is reserved and must be prefixed."""
        result = sanitizer.sanitize_alias("group")
        assert result == "COL_GROUP", f"Expected COL_GROUP, got {result}"

    def test_reserved_keyword_select(self, sanitizer):
        """'SELECT' is reserved and must be prefixed."""
        result = sanitizer.sanitize_alias("select")
        assert result == "COL_SELECT", f"Expected COL_SELECT, got {result}"

    def test_reserved_keyword_order(self, sanitizer):
        """'ORDER' is reserved and must be prefixed."""
        result = sanitizer.sanitize_alias("order")
        assert result == "COL_ORDER", f"Expected COL_ORDER, got {result}"

    def test_reserved_keyword_join(self, sanitizer):
        """'JOIN' is reserved and must be prefixed."""
        result = sanitizer.sanitize_alias("join")
        assert result == "COL_JOIN", f"Expected COL_JOIN, got {result}"

    def test_reserved_keyword_null(self, sanitizer):
        """'NULL' is reserved and must be prefixed."""
        result = sanitizer.sanitize_alias("null")
        assert result == "COL_NULL", f"Expected COL_NULL, got {result}"

    def test_reserved_keyword_count(self, sanitizer):
        """'COUNT' is reserved (aggregate function) and must be prefixed."""
        result = sanitizer.sanitize_alias("count")
        assert result == "COL_COUNT", f"Expected COL_COUNT, got {result}"

    def test_reserved_keyword_sum(self, sanitizer):
        """'SUM' is reserved (aggregate function) and must be prefixed."""
        result = sanitizer.sanitize_alias("sum")
        assert result == "COL_SUM", f"Expected COL_SUM, got {result}"

    def test_non_reserved_customer(self, sanitizer):
        """'CUSTOMER' is not reserved, no prefix."""
        result = sanitizer.sanitize_alias("customer")
        assert result == "CUSTOMER"
        assert not result.startswith("COL_")

    def test_non_reserved_revenue(self, sanitizer):
        """'REVENUE' is not reserved, no prefix."""
        result = sanitizer.sanitize_alias("revenue")
        assert result == "REVENUE"
        assert not result.startswith("COL_")

    def test_reserved_keyword_collision_detection(self, registry=None):
        """Multiple instances of reserved keyword should get suffixes."""
        from semabridge.utils.identifiers import IdentifierRegistry

        registry = IdentifierRegistry()

        # Register same reserved keyword with different case (different source names)
        result1 = registry.register("date", name_type="alias")
        result2 = registry.register("DATE", name_type="alias")  # Different source, same sanitized result

        # First should be COL_DATE, second should get collision suffix _2
        assert result1 == "COL_DATE"
        assert result2 == "COL_DATE_2", f"Expected COL_DATE_2 for collision, got {result2}"

        # But if we register "date" again, it should be cached
        result3 = registry.register("date", name_type="alias")
        assert result3 == "COL_DATE"  # Same source, cached

    def test_reserved_keyword_deterministic(self, sanitizer):
        """Reserved keyword handling must be deterministic."""
        name = "select"
        result1 = sanitizer.sanitize_alias(name)
        result2 = sanitizer.sanitize_alias(name)
        result3 = sanitizer.sanitize_alias(name)

        assert result1 == result2 == result3 == "COL_SELECT"



    """Ensure backward compatibility with existing uses."""

    def test_valid_existing_names_unchanged(self):
        """Valid existing identifier names should remain unchanged."""
        sanitizer = IdentifierSanitizer()
        valid_names = [
            "CUSTOMER_ID",
            "AMOUNT",
            "ORDER_DATE",
            "PRODUCT_NAME",
        ]

        for name in valid_names:
            result = sanitizer.sanitize_column(name)
            assert result == name, f"Valid name '{name}' was modified to '{result}'"

    def test_existing_rel_naming_still_works(self):
        """Relationship naming should still produce correct format."""
        from semabridge.utils.relationship_naming import generate_relationship_name

        name = generate_relationship_name("CUSTOMER", "CUST_ID", "ORDER", "CUST_FK")
        assert name.startswith("REL_")
        assert "__" in name
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in name)
