"""
Tests for Module 1: Pre-Deployment State Validation Engine.

Verifies that Tier 6 (Snowflake metadata validation) fires when
``snowflake_metadata`` is provided to ``GlobalValidator``, and correctly
detects missing tables and columns.
"""

import pytest
from unittest.mock import MagicMock, patch

from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.core.validation.global_validator import (
    GlobalValidator,
    GlobalValidationReport,
)


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
def sf_behavior():
    """Minimal SnowflakeBehavior stub."""
    beh = MagicMock()
    beh.pk_resolution_mode = MagicMock(value="permissive")
    beh.create_missing_tables = False
    return beh


def _make_column(name, data_type="STRING", source_expression=None, is_key=False):
    col = MagicMock()
    col.unique_name = name
    col.data_type = MagicMock(value=data_type)
    col.source_expression = source_expression
    col.is_key = is_key
    col.is_calculated = False
    return col


def _make_dataset(name, columns, source_table=None):
    ds = MagicMock()
    ds.unique_name = name
    ds.columns = columns
    ds.source_table = source_table or name
    return ds


def _make_metric(name, dataset, source_column, aggregation="sum"):
    m = MagicMock()
    m.unique_name = name
    m.dataset = dataset
    m.source_column = source_column
    m.aggregation = MagicMock(value=aggregation)
    m.sql_expression = None
    m.expression = None
    m.label = name
    return m


def _make_model(name, datasets, metrics=None, relationships=None, dimensions=None):
    model = MagicMock()
    model.unique_name = name
    model.datasets = datasets
    model.metrics = metrics or []
    model.relationships = relationships or []
    model.dimensions = dimensions or []
    return model


# ---------------------------------------------------------------------------
# Tests: Tier 6 fires when metadata is provided
# ---------------------------------------------------------------------------

class TestTier6MetadataValidation:
    """Verify Tier 6 catches missing tables/columns against live metadata."""

    def test_tier6_missing_table_detected(self, sanitizer, sf_behavior):
        """When a dataset references a table not in INFORMATION_SCHEMA,
        Tier 6 should report an ERROR."""
        # Snowflake has TABLE_A but not FABRICMODEL_DATA
        sf_meta = {
            "TABLE_A": {"COL_X", "COL_Y"},
        }
        ds = _make_dataset("Employee", [
            _make_column("Name"),
            _make_column("Salary"),
        ], source_table="FABRICMODEL_DATA")

        model = _make_model("TestModel", [ds])
        validator = GlobalValidator(sanitizer, sf_behavior, snowflake_metadata=sf_meta)
        report = validator.validate(model, halt_on_error=False)

        tier6_errors = [
            i for i in report.issues
            if i.tier == 6 and i.severity == "ERROR"
        ]
        assert len(tier6_errors) >= 1
        assert "FABRICMODEL_DATA" in tier6_errors[0].message

    def test_tier6_missing_column_detected(self, sanitizer, sf_behavior):
        """When a column is not found in the Snowflake table, Tier 6
        should report a WARNING."""
        sf_meta = {
            "FACT_SALES": {"AMOUNT", "DATE_KEY", "PRODUCT_ID"},
        }
        ds = _make_dataset("FactSales", [
            _make_column("Amount"),
            _make_column("CLUSTERID"),  # missing in Snowflake
        ], source_table="FACT_SALES")

        model = _make_model("TestModel", [ds])
        validator = GlobalValidator(sanitizer, sf_behavior, snowflake_metadata=sf_meta)
        report = validator.validate(model, halt_on_error=False)

        tier6_warnings = [
            i for i in report.issues
            if i.tier == 6 and i.severity == "WARNING"
        ]
        assert any("CLUSTERID" in w.message for w in tier6_warnings)

    def test_tier6_all_columns_match(self, sanitizer, sf_behavior):
        """When all columns match, Tier 6 produces no issues."""
        sf_meta = {
            "FACT_SALES": {"AMOUNT", "DATE_KEY", "PRODUCT_ID"},
        }
        ds = _make_dataset("FactSales", [
            _make_column("Amount"),
            _make_column("Date_Key"),
            _make_column("Product_Id"),
        ], source_table="FACT_SALES")

        model = _make_model("TestModel", [ds])
        validator = GlobalValidator(sanitizer, sf_behavior, snowflake_metadata=sf_meta)
        report = validator.validate(model, halt_on_error=False)

        tier6_issues = [i for i in report.issues if i.tier == 6]
        assert len(tier6_issues) == 0

    def test_tier6_skipped_when_no_metadata(self, sanitizer, sf_behavior):
        """Without snowflake_metadata, Tier 6 should not run."""
        ds = _make_dataset("MissingTable", [
            _make_column("Anything"),
        ], source_table="NONEXISTENT_TABLE")

        model = _make_model("TestModel", [ds])
        # No snowflake_metadata
        validator = GlobalValidator(sanitizer, sf_behavior)
        report = validator.validate(model, halt_on_error=False)

        tier6_issues = [i for i in report.issues if i.tier == 6]
        assert len(tier6_issues) == 0

    def test_tier6_skips_calculated_columns(self, sanitizer, sf_behavior):
        """Calculated columns should be excluded from Tier 6 checks."""
        sf_meta = {
            "FACT_SALES": {"AMOUNT"},
        }
        ds = _make_dataset("FactSales", [
            _make_column("Amount"),
            _make_column("CalcCol", source_expression="SUM(Amount) / COUNT(*)"),
        ], source_table="FACT_SALES")

        model = _make_model("TestModel", [ds])
        validator = GlobalValidator(sanitizer, sf_behavior, snowflake_metadata=sf_meta)
        report = validator.validate(model, halt_on_error=False)

        # CalcCol should NOT be reported as missing
        tier6_issues = [i for i in report.issues if i.tier == 6]
        assert not any("CALCCOL" in i.message for i in tier6_issues)

    def test_tier6_skips_internal_columns(self, sanitizer, sf_behavior):
        """RowNumber and _-prefixed columns should be excluded."""
        sf_meta = {
            "FACT_SALES": {"AMOUNT"},
        }
        ds = _make_dataset("FactSales", [
            _make_column("Amount"),
            _make_column("RowNumber-1234"),
            _make_column("_internal"),
        ], source_table="FACT_SALES")

        model = _make_model("TestModel", [ds])
        validator = GlobalValidator(sanitizer, sf_behavior, snowflake_metadata=sf_meta)
        report = validator.validate(model, halt_on_error=False)

        tier6_issues = [i for i in report.issues if i.tier == 6]
        assert len(tier6_issues) == 0


# ---------------------------------------------------------------------------
# Tests: _fetch_schema_metadata in SnowflakeEmitter
# ---------------------------------------------------------------------------

class TestFetchSchemaMetadata:
    """Verify the emitter's _fetch_schema_metadata() helper."""

    def test_returns_dict_of_sets(self):
        """Metadata should be {TABLE_NAME: {COL_SET}}."""
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        # Build a mock emitter with mock config
        config = MagicMock()
        config.database = "ANALYTICS_DB"
        config.schema_name = "SEMANTIC_LAYER"

        emitter = SnowflakeEmitter.__new__(SnowflakeEmitter)
        emitter.config = config

        # Mock cursor
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            ("FACT_SALES", "AMOUNT"),
            ("FACT_SALES", "DATE_KEY"),
            ("DIM_PRODUCT", "PRODUCT_ID"),
            ("DIM_PRODUCT", "PRODUCT_NAME"),
        ]

        result = emitter._fetch_schema_metadata(cursor)

        assert "FACT_SALES" in result
        assert "DIM_PRODUCT" in result
        assert result["FACT_SALES"] == {"AMOUNT", "DATE_KEY"}
        assert result["DIM_PRODUCT"] == {"PRODUCT_ID", "PRODUCT_NAME"}

    def test_returns_empty_on_error(self):
        """On query failure, returns empty dict (graceful degradation)."""
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        config = MagicMock()
        config.database = "DB"
        config.schema_name = "SCH"

        emitter = SnowflakeEmitter.__new__(SnowflakeEmitter)
        emitter.config = config

        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Connection timeout")

        result = emitter._fetch_schema_metadata(cursor)
        assert result == {}


# ---------------------------------------------------------------------------
# Tests: _check_semantic_view_exists
# ---------------------------------------------------------------------------

class TestCheckSemanticViewExists:
    """Verify SHOW SEMANTIC VIEWS check."""

    def test_returns_true_when_exists(self):
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        emitter = SnowflakeEmitter.__new__(SnowflakeEmitter)
        cursor = MagicMock()
        cursor.fetchall.return_value = [("row",)]

        assert emitter._check_semantic_view_exists(cursor, "My Model") is True

    def test_returns_false_when_missing(self):
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        emitter = SnowflakeEmitter.__new__(SnowflakeEmitter)
        cursor = MagicMock()
        cursor.fetchall.return_value = []

        assert emitter._check_semantic_view_exists(cursor, "Missing") is False

    def test_returns_false_on_error(self):
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        emitter = SnowflakeEmitter.__new__(SnowflakeEmitter)
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Permission denied")

        assert emitter._check_semantic_view_exists(cursor, "NoAccess") is False
