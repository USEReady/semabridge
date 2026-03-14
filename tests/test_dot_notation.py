"""
Tests for Module 2: Lexical Identifier Normalization and Quoting Algorithm.

Verifies the centralized dot-notation resolution, table ref validation,
split_dot_identifier, and the updated sanitize_column() behavior.
"""

import pytest

from semabridge.utils.identifiers import IdentifierSanitizer, SQL_FUNCTION_NAMES
from semabridge.utils.identifier_normalizer import IdentifierNormalizer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sanitizer():
    return IdentifierSanitizer(
        force_uppercase=True,
        always_quote=True,
        suppress_reserved=True,
    )


@pytest.fixture
def normalizer(sanitizer):
    return IdentifierNormalizer(sanitizer)


# ---------------------------------------------------------------------------
# sanitize_column() dot-notation handling
# ---------------------------------------------------------------------------

class TestSanitizeColumnDotNotation:
    """Verify sanitize_column() now splits TABLE.COLUMN → COLUMN."""

    def test_simple_column(self, sanitizer):
        assert sanitizer.sanitize_column("Amount") == "AMOUNT"

    def test_dot_notation_two_parts(self, sanitizer):
        """TABLE.COLUMN → COLUMN."""
        assert sanitizer.sanitize_column("DEVICE_INVENTORY.CATEGORY") == "CATEGORY"

    def test_dot_notation_l_date_monthno(self, sanitizer):
        """L_DATE.MONTHNO → MONTHNO."""
        assert sanitizer.sanitize_column("L_DATE.MONTHNO") == "MONTHNO"

    def test_dot_notation_preserves_simple(self, sanitizer):
        """A column without a dot remains unchanged."""
        assert sanitizer.sanitize_column("FORECAST_ADJUSTMENT") == "FORECAST_ADJUSTMENT"

    def test_dot_notation_three_parts_not_split(self, sanitizer):
        """DB.SCHEMA.TABLE is NOT a column reference — falls through."""
        result = sanitizer.sanitize_column("DB.SCHEMA.TABLE")
        # 3-part name: dots become underscores (legacy behavior)
        assert result == "DB_SCHEMA_TABLE"

    def test_dot_notation_quoted_not_split(self, sanitizer):
        """Quoted identifiers bypass dot-splitting."""
        result = sanitizer.sanitize_column('"My.Column"')
        # Starts with quote → no dot splitting, clean non-alnum
        assert "MY" in result

    def test_dot_notation_with_spaces_not_split(self, sanitizer):
        """Dot between non-identifier text → fallback to underscore."""
        # "Hello World.Foo Bar" → parts have spaces → not simple identifiers
        result = sanitizer.sanitize_column("Hello World.Foo Bar")
        assert result == "HELLO_WORLD_FOO_BAR"

    def test_dax_bracket_qualifier(self, sanitizer):
        """DAX 'Table'[Column] → COLUMN."""
        assert sanitizer.sanitize_column("'Sales'[Amount]") == "AMOUNT"


# ---------------------------------------------------------------------------
# resolve_dot_notation()
# ---------------------------------------------------------------------------

class TestResolveDotNotation:
    """Test centralized TABLE.COLUMN rewriting in expressions."""

    def test_rewrites_known_table(self, sanitizer):
        expr = "FACT.AMOUNT + DIM.PRICE"
        alias_lookup = {"FACT": '"L_FACT"', "DIM": '"L_DIM"'}
        result = sanitizer.resolve_dot_notation(expr, alias_lookup)
        assert '"L_FACT"."AMOUNT"' in result
        assert '"L_DIM"."PRICE"' in result

    def test_leaves_unknown_table(self, sanitizer):
        expr = "UNKNOWN.COLUMN + FACT.AMOUNT"
        alias_lookup = {"FACT": '"L_FACT"'}
        result = sanitizer.resolve_dot_notation(expr, alias_lookup)
        assert "UNKNOWN.COLUMN" in result
        assert '"L_FACT"."AMOUNT"' in result

    def test_preserves_quoted_column(self, sanitizer):
        """TABLE."Already Quoted" → alias."Already Quoted"."""
        expr = 'FACT."My Col"'
        alias_lookup = {"FACT": '"L_FACT"'}
        result = sanitizer.resolve_dot_notation(expr, alias_lookup)
        assert '"L_FACT"."My Col"' in result

    def test_custom_sanitize_fn(self, sanitizer):
        """Custom sanitize function is used for column names."""
        expr = "FACT.my_col"
        alias_lookup = {"FACT": '"T_FACT"'}
        result = sanitizer.resolve_dot_notation(
            expr, alias_lookup,
            sanitize_col_fn=lambda c: c.lower(),  # lowercase instead
        )
        assert '"T_FACT"."my_col"' in result

    def test_no_dots_returns_unchanged(self, sanitizer):
        expr = 'SUM("AMOUNT")'
        result = sanitizer.resolve_dot_notation(expr, {"FACT": '"F"'})
        assert result == expr

    def test_multiple_refs_in_expression(self, sanitizer):
        expr = "COALESCE(DIM.NAME, FACT.DEFAULT_NAME)"
        alias_lookup = {"DIM": '"D"', "FACT": '"F"'}
        result = sanitizer.resolve_dot_notation(expr, alias_lookup)
        assert '"D"."NAME"' in result
        assert '"F"."DEFAULT_NAME"' in result


# ---------------------------------------------------------------------------
# validate_table_refs()
# ---------------------------------------------------------------------------

class TestValidateTableRefs:
    """Test defence-in-depth table reference validation."""

    def test_no_invalid_refs(self, sanitizer):
        expr = '"L_FACT"."AMOUNT" + "L_DIM"."PRICE"'
        valid = {'"L_FACT"', '"L_DIM"'}
        invalid = sanitizer.validate_table_refs(expr, valid)
        assert invalid == []

    def test_detects_invalid_ref(self, sanitizer):
        expr = 'UNKNOWN_TABLE."AMOUNT" + "L_FACT"."PRICE"'
        valid = {'"L_FACT"'}
        invalid = sanitizer.validate_table_refs(expr, valid)
        assert "UNKNOWN_TABLE" in invalid

    def test_sql_functions_excluded(self, sanitizer):
        expr = "SUM.something + COALESCE.other"
        valid = set()
        invalid = sanitizer.validate_table_refs(expr, valid)
        # SUM and COALESCE are in SQL_FUNCTION_NAMES → not invalid
        assert "SUM" not in invalid
        assert "COALESCE" not in invalid

    def test_empty_expression(self, sanitizer):
        assert sanitizer.validate_table_refs("", set()) == []


# ---------------------------------------------------------------------------
# split_dot_identifier()
# ---------------------------------------------------------------------------

class TestSplitDotIdentifier:
    """Test static split_dot_identifier method."""

    def test_simple_column(self):
        table, col = IdentifierSanitizer.split_dot_identifier("AMOUNT")
        assert table is None
        assert col == "AMOUNT"

    def test_two_part(self):
        table, col = IdentifierSanitizer.split_dot_identifier("TABLE.COLUMN")
        assert table == "TABLE"
        assert col == "COLUMN"

    def test_three_part(self):
        table, col = IdentifierSanitizer.split_dot_identifier("SCHEMA.TABLE.COL")
        assert table == "TABLE"
        assert col == "COL"

    def test_four_part(self):
        table, col = IdentifierSanitizer.split_dot_identifier("DB.SCH.TBL.COL")
        assert table == "TBL"
        assert col == "COL"

    def test_empty_string(self):
        table, col = IdentifierSanitizer.split_dot_identifier("")
        assert table is None
        assert col == ""

    def test_no_dot(self):
        table, col = IdentifierSanitizer.split_dot_identifier("NODOT")
        assert table is None
        assert col == "NODOT"

    def test_quoted_segment(self):
        """Dots inside quotes should NOT split."""
        table, col = IdentifierSanitizer.split_dot_identifier(
            '"My.Table"."My.Column"'
        )
        assert table == '"My.Table"'
        assert col == '"My.Column"'


# ---------------------------------------------------------------------------
# IdentifierNormalizer.split_qualified_identifier()
# ---------------------------------------------------------------------------

class TestNormalizerSplitQualified:
    """Test split_qualified_identifier with sanitization."""

    def test_simple_column_normalized(self, normalizer):
        table, col = normalizer.split_qualified_identifier("amount")
        assert table is None
        assert col == "AMOUNT"

    def test_two_part_normalized(self, normalizer):
        table, col = normalizer.split_qualified_identifier("Sales Table.Amount Col")
        assert table == "SALES_TABLE"
        assert col == "AMOUNT_COL"

    def test_three_part_normalized(self, normalizer):
        table, col = normalizer.split_qualified_identifier("PUBLIC.FACT_SALES.TOTAL")
        assert table == "FACT_SALES"
        assert col == "TOTAL"
