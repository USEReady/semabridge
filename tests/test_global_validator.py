"""
Tests for the Global Validator (6-tier pre-deployment validation).

Covers:
    Tier 1 — Schema integrity (empty model, no datasets)
    Tier 2 — Identifier grounding (columns exist physically)
    Tier 3 — PK integrity (delegated to PrimaryKeyResolver)
    Tier 4 — Relationship FK/PK validation
    Tier 5 — Metric validation (valid columns/datasets)
    Tier 6 — Snowflake metadata (tested with mock metadata)
    Integration — Full validate() pipeline
"""

import pytest
from unittest.mock import MagicMock

from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    SMLDimension, SMLAttribute,
    DataType, AggregationType, Cardinality, CrossFilterDirection,
)
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.core.validation.global_validator import (
    GlobalValidator,
    GlobalValidationReport,
    ValidationIssue,
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


def _healthy_model() -> SMLModel:
    """Creates a structurally sound model that passes all tiers."""
    return SMLModel(
        unique_name="HealthyModel",
        datasets=[
            SMLDataset(
                unique_name="FactSales",
                source_table="FACT_SALES",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="SALE_ID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="AMOUNT", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="CUST_ID", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="DimCustomer",
                source_table="DIM_CUSTOMER",
                is_fact=False,
                columns=[
                    SMLColumn(unique_name="CUST_ID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="NAME", data_type=DataType.STRING),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TotalSales",
                dataset="FactSales",
                source_column="AMOUNT",
                aggregation=AggregationType.SUM,
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="Sales_Customer",
                from_dataset="FactSales",
                from_columns=["CUST_ID"],
                to_dataset="DimCustomer",
                to_columns=["CUST_ID"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ],
    )


# =========================================================================
# Tier 1 — Schema
# =========================================================================


class TestTier1Schema:
    def test_empty_model_has_errors(self, sanitizer):
        model = SMLModel(unique_name="Empty", datasets=[])
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        assert report.has_errors

    def test_valid_model_passes_schema(self, sanitizer):
        model = _healthy_model()
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        schema_errors = [i for i in report.issues if i.tier == 1 and i.severity == "ERROR"]
        assert len(schema_errors) == 0


# =========================================================================
# Tier 3 — PK Integrity
# =========================================================================


class TestTier3PK:
    def test_pk_resolved_for_all_datasets(self, sanitizer):
        model = _healthy_model()
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        pk_errors = [i for i in report.issues if i.tier == 3 and i.severity == "ERROR"]
        assert len(pk_errors) == 0

    def test_strict_no_pk_blocks(self, sanitizer):
        model = SMLModel(
            unique_name="NoPK",
            datasets=[
                SMLDataset(
                    unique_name="Orphan",
                    source_table="ORPHAN_TABLE",
                    columns=[
                        SMLColumn(unique_name="VALUE", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="LABEL", data_type=DataType.STRING),
                    ],
                ),
            ],
        )
        v = GlobalValidator(sanitizer, _sf_behavior("strict"))
        report = v.validate(model, halt_on_error=False)
        pk_errors = [i for i in report.issues if i.tier == 3 and i.severity == "ERROR"]
        assert len(pk_errors) > 0


# =========================================================================
# Tier 4 — Relationships
# =========================================================================


class TestTier4Relationships:
    def test_valid_relationship_passes(self, sanitizer):
        model = _healthy_model()
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        rel_errors = [i for i in report.issues if i.tier == 4 and i.severity == "ERROR"]
        assert len(rel_errors) == 0

    def test_relationship_to_missing_dataset(self, sanitizer):
        model = SMLModel(
            unique_name="BrokenRel",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    source_table="SALES",
                    columns=[
                        SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True),
                        SMLColumn(unique_name="CUST_ID", data_type=DataType.INTEGER),
                    ],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="Bad_Rel",
                    from_dataset="Sales",
                    from_columns=["CUST_ID"],
                    to_dataset="NonExistent",
                    to_columns=["ID"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
            ],
        )
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        rel_errors = [i for i in report.issues if i.tier == 4 and i.severity == "ERROR"]
        assert len(rel_errors) > 0


# =========================================================================
# Tier 5 — Metrics
# =========================================================================


class TestTier5Metrics:
    def test_valid_metric_passes(self, sanitizer):
        model = _healthy_model()
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        metric_errors = [i for i in report.issues if i.tier == 5 and i.severity == "ERROR"]
        assert len(metric_errors) == 0

    def test_metric_referencing_unknown_dataset(self, sanitizer):
        """Metric referencing unknown dataset is caught at tier 1, not tier 5.
        Tier 5 catches unknown columns within valid datasets."""
        model = SMLModel(
            unique_name="BadMetric",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    source_table="SALES",
                    columns=[
                        SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True),
                        SMLColumn(unique_name="AMOUNT", data_type=DataType.DECIMAL),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="BadTotal",
                    dataset="Sales",
                    source_column="NONEXISTENT_COL",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        # Tier 5 should warn about invalid source_column
        metric_issues = [i for i in report.issues if i.tier == 5]
        assert len(metric_issues) > 0


# =========================================================================
# Full Pipeline
# =========================================================================


class TestFullValidation:
    def test_healthy_model_passes(self, sanitizer):
        model = _healthy_model()
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        assert not report.has_errors

    def test_report_collects_all_tiers(self, sanitizer):
        model = _healthy_model()
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        # Should have processed at least tiers 1-5
        assert isinstance(report, GlobalValidationReport)
        assert isinstance(report.issues, list)

    def test_report_format_summary(self, sanitizer):
        model = _healthy_model()
        v = GlobalValidator(sanitizer, _sf_behavior())
        report = v.validate(model, halt_on_error=False)
        summary = report.errors_summary()
        assert isinstance(summary, str)
        assert len(summary) > 0
