"""
Tests for IdentifierNormalizer.

Validates identifier normalization, column existence checks,
alias lookup building (including reserved-word-prefixed aliases),
and casing mismatch detection.
"""

import pytest

from semabridge.utils.identifier_normalizer import IdentifierNormalizer
from semabridge.utils.identifiers import IdentifierSanitizer


@pytest.fixture
def normalizer():
    """Create an IdentifierNormalizer with default settings."""
    sanitizer = IdentifierSanitizer(
        force_uppercase=True,
        always_quote=True,
        suppress_reserved=True,
    )
    return IdentifierNormalizer(sanitizer)


class TestNormalizeIdentifier:
    """Tests for normalize_identifier()."""

    def test_unquoted_uppercased(self, normalizer):
        result = normalizer.normalize_identifier("revenue")
        assert result == "REVENUE"

    def test_quoted_preserves_case(self, normalizer):
        result = normalizer.normalize_identifier("Revenue", quoted=True)
        assert result == "Revenue"

    def test_special_chars_replaced(self, normalizer):
        result = normalizer.normalize_identifier("Total Revenue")
        assert result == "TOTAL_REVENUE"

    def test_dax_qualifier_stripped(self, normalizer):
        result = normalizer.normalize_identifier("'Table'[Column]")
        assert "COLUMN" in result


class TestValidateColumnExists:
    """Tests for validate_column_exists()."""

    def test_column_exists(self, normalizer):
        cols = {"REVENUE", "COST", "ORDER_ID"}
        assert normalizer.validate_column_exists(cols, "Revenue") is True

    def test_column_not_exists(self, normalizer):
        cols = {"REVENUE", "COST"}
        assert normalizer.validate_column_exists(cols, "NONEXISTENT") is False

    def test_empty_columns(self, normalizer):
        assert normalizer.validate_column_exists(set(), "Revenue") is False


class TestBuildAliasLookup:
    """Tests for build_alias_lookup()."""

    def test_basic_lookup(self, normalizer):
        """Aliases and unique_names are in the lookup."""
        from unittest.mock import MagicMock

        ds = MagicMock()
        ds.unique_name = "Sales"
        ds.source_table = "FACT_SALES"

        lookup = normalizer.build_alias_lookup(
            [ds], {"Sales": "SALES"}
        )

        assert lookup["SALES"] == "SALES"  # unique_name upper

    def test_reserved_word_alias_in_lookup(self, normalizer):
        """Reserved-word aliases (e.g. L_TABLE) must be discoverable."""
        from unittest.mock import MagicMock

        ds = MagicMock()
        ds.unique_name = "Table"
        ds.source_table = None

        # sanitize_alias("Table") → "L_TABLE" (reserved word prefix)
        lookup = normalizer.build_alias_lookup(
            [ds], {"Table": "L_TABLE"}
        )

        # The alias itself must be in the lookup
        assert lookup.get("L_TABLE") == "L_TABLE"
        # The original name maps to the alias
        assert lookup.get("TABLE") == "L_TABLE"

    def test_source_table_variant(self, normalizer):
        """source_table name should also map to the alias."""
        from unittest.mock import MagicMock

        ds = MagicMock()
        ds.unique_name = "DimProduct"
        ds.source_table = "DIM_PRODUCT"

        lookup = normalizer.build_alias_lookup(
            [ds], {"DimProduct": "DIMPRODUCT"}
        )

        assert lookup.get("DIM_PRODUCT") == "DIMPRODUCT"


class TestDetectCasingMismatches:
    """Tests for detect_casing_mismatches()."""

    def test_no_mismatches(self, normalizer):
        model_cols = {"REVENUE", "COST"}
        phys_cols = {"REVENUE", "COST"}
        assert normalizer.detect_casing_mismatches(model_cols, phys_cols) == []

    def test_casing_mismatch_detected(self, normalizer):
        model_cols = {"REVENUE", "cost"}
        phys_cols = {"REVENUE", "COST"}
        mismatches = normalizer.detect_casing_mismatches(model_cols, phys_cols)
        assert len(mismatches) == 1
        assert mismatches[0] == ("cost", "COST")

    def test_missing_column_not_mismatch(self, normalizer):
        model_cols = {"REVENUE", "MISSING"}
        phys_cols = {"REVENUE"}
        mismatches = normalizer.detect_casing_mismatches(model_cols, phys_cols)
        assert len(mismatches) == 0
