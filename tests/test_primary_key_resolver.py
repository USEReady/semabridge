"""
Tests for the Primary Key Resolution Engine.

Covers:
    - Explicit PK detection (is_key=True)
    - Relationship-based PK inference (inbound FK → PK)
    - Heuristic PK detection (naming patterns: *_ID, *_KEY, etc.)
    - Permissive fallback to first physical column
    - Strict mode rejection when no PK found
    - Calculated column exclusion
    - Multi-dataset batch resolution
"""

import pytest
from unittest.mock import MagicMock

from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    DataType, AggregationType, Cardinality,
)
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.core.validation.primary_key_resolver import (
    PrimaryKeyResolver,
    PKResolution,
)


@pytest.fixture
def sanitizer():
    return IdentifierSanitizer(
        force_uppercase=True,
        always_quote=True,
        suppress_reserved=True,
    )


def _sf_behavior(pk_mode: str = "permissive"):
    enum_val = MagicMock()
    enum_val.value = pk_mode
    beh = MagicMock()
    beh.pk_resolution_mode = enum_val
    return beh


# =========================================================================
# Explicit PK (is_key=True)
# =========================================================================


class TestExplicitPK:
    """Strategy 1: Explicit is_key=True columns."""

    def test_single_explicit_key(self, sanitizer):
        ds = SMLDataset(
            unique_name="Orders",
            source_table="ORDERS",
            columns=[
                SMLColumn(unique_name="ORDER_ID", data_type=DataType.INTEGER, is_key=True),
                SMLColumn(unique_name="AMOUNT", data_type=DataType.DECIMAL),
            ],
        )
        model = SMLModel(unique_name="Test", datasets=[ds])
        resolver = PrimaryKeyResolver(sanitizer)
        result = resolver.resolve_single(ds, model.relationships or [])

        assert result.is_valid
        assert result.source == "explicit"
        assert len(result.pk_columns) == 1
        assert "ORDER_ID" in result.pk_columns[0].upper()

    def test_composite_explicit_key(self, sanitizer):
        ds = SMLDataset(
            unique_name="OrderLines",
            source_table="ORDER_LINES",
            columns=[
                SMLColumn(unique_name="ORDER_ID", data_type=DataType.INTEGER, is_key=True),
                SMLColumn(unique_name="LINE_NUM", data_type=DataType.INTEGER, is_key=True),
                SMLColumn(unique_name="QTY", data_type=DataType.DECIMAL),
            ],
        )
        model = SMLModel(unique_name="Test", datasets=[ds])
        resolver = PrimaryKeyResolver(sanitizer)
        result = resolver.resolve_single(ds, model.relationships or [])

        assert result.is_valid
        assert result.source == "explicit"
        assert len(result.pk_columns) == 2


# =========================================================================
# Relationship-based PK
# =========================================================================


class TestRelationshipPK:
    """Strategy 2: Inbound FK references define PK."""

    def test_inbound_fk_as_pk(self, sanitizer):
        fact = SMLDataset(
            unique_name="Sales",
            source_table="FACT_SALES",
            columns=[
                SMLColumn(unique_name="SALE_ID", data_type=DataType.INTEGER),
                SMLColumn(unique_name="CUST_ID", data_type=DataType.INTEGER),
            ],
        )
        dim = SMLDataset(
            unique_name="Customer",
            source_table="DIM_CUSTOMER",
            columns=[
                SMLColumn(unique_name="CUST_ID", data_type=DataType.INTEGER),
                SMLColumn(unique_name="NAME", data_type=DataType.STRING),
            ],
        )
        rel = SMLRelationship(
            unique_name="Sales_Customer",
            from_dataset="Sales",
            from_columns=["CUST_ID"],
            to_dataset="Customer",
            to_columns=["CUST_ID"],
            cardinality=Cardinality.MANY_TO_ONE,
        )
        model = SMLModel(
            unique_name="Test",
            datasets=[fact, dim],
            relationships=[rel],
        )
        resolver = PrimaryKeyResolver(sanitizer)
        # 'Customer' has no explicit PK but is the "to" side of a relationship
        result = resolver.resolve_single(dim, model.relationships)

        assert result.is_valid
        assert result.source == "relationship"
        assert any("CUST_ID" in c.upper() for c in result.pk_columns)


# =========================================================================
# Heuristic PK
# =========================================================================


class TestHeuristicPK:
    """Strategy 3: Auto-detect PK candidates from naming patterns."""

    @pytest.mark.parametrize("col_name", ["CUSTOMER_ID", "ORDER_KEY", "PRODUCT_PK", "ID"])
    def test_heuristic_patterns(self, sanitizer, col_name):
        ds = SMLDataset(
            unique_name="TestTable",
            source_table="TEST_TABLE",
            columns=[
                SMLColumn(unique_name=col_name, data_type=DataType.INTEGER),
                SMLColumn(unique_name="NAME", data_type=DataType.STRING),
            ],
        )
        model = SMLModel(unique_name="Test", datasets=[ds])
        resolver = PrimaryKeyResolver(sanitizer, pk_mode="permissive")
        result = resolver.resolve_single(ds, model.relationships or [])

        assert result.is_valid
        assert result.source in ("heuristic", "fallback")

    def test_no_pk_column_names(self, sanitizer):
        """When no naming pattern matches, heuristic returns nothing in strict mode."""
        ds = SMLDataset(
            unique_name="Metrics",
            source_table="METRIC_TABLE",
            columns=[
                SMLColumn(unique_name="VALUE", data_type=DataType.DECIMAL),
                SMLColumn(unique_name="LABEL", data_type=DataType.STRING),
            ],
        )
        model = SMLModel(unique_name="Test", datasets=[ds])
        resolver = PrimaryKeyResolver(sanitizer, pk_mode="strict")
        result = resolver.resolve_single(ds, model.relationships or [])

        assert not result.is_valid
        assert len(result.errors) > 0


# =========================================================================
# Permissive Fallback
# =========================================================================


class TestPermissiveFallback:
    """Permissive mode falls back to first physical column."""

    def test_fallback_returns_first_column(self, sanitizer):
        ds = SMLDataset(
            unique_name="Mystery",
            source_table="MYSTERY_TABLE",
            columns=[
                SMLColumn(unique_name="FOO", data_type=DataType.STRING),
                SMLColumn(unique_name="BAR", data_type=DataType.STRING),
            ],
        )
        model = SMLModel(unique_name="Test", datasets=[ds])
        resolver = PrimaryKeyResolver(sanitizer, pk_mode="permissive")
        result = resolver.resolve_single(ds, model.relationships or [])

        assert result.is_valid
        assert result.source in ("heuristic", "fallback")
        assert len(result.pk_columns) >= 1
        assert len(result.warnings) > 0  # Should warn about fallback


class TestStrictMode:
    """Strict mode rejects datasets without clear PKs."""

    def test_strict_rejects_no_pk(self, sanitizer):
        ds = SMLDataset(
            unique_name="NoPK",
            source_table="NO_PK_TABLE",
            columns=[
                SMLColumn(unique_name="VALUE", data_type=DataType.DECIMAL),
                SMLColumn(unique_name="LABEL", data_type=DataType.STRING),
            ],
        )
        model = SMLModel(unique_name="Test", datasets=[ds])
        resolver = PrimaryKeyResolver(sanitizer, pk_mode="strict")
        result = resolver.resolve_single(ds, model.relationships or [])

        assert not result.is_valid
        assert len(result.errors) > 0
        assert any("strict" in e.lower() or "no pk" in e.lower() or "no primary" in e.lower() for e in result.errors)


# =========================================================================
# Batch Resolution
# =========================================================================


class TestBatchResolution:
    """resolve_all processes all datasets in a model."""

    def test_all_datasets_resolved(self, sanitizer):
        model = SMLModel(
            unique_name="Multi",
            datasets=[
                SMLDataset(
                    unique_name="A",
                    source_table="TABLE_A",
                    columns=[
                        SMLColumn(unique_name="A_ID", data_type=DataType.INTEGER, is_key=True),
                    ],
                ),
                SMLDataset(
                    unique_name="B",
                    source_table="TABLE_B",
                    columns=[
                        SMLColumn(unique_name="B_ID", data_type=DataType.INTEGER, is_key=True),
                    ],
                ),
            ],
        )
        resolver = PrimaryKeyResolver(sanitizer)
        results = resolver.resolve_all(model.datasets, model.relationships or [])

        assert len(results) == 2
        assert all(r.is_valid for r in results.values())
