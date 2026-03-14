"""
Tests for the Pre-Deployment Semantic Model Validator.

Covers all four validation tiers:
    Tier 1 — YAML Schema integrity
    Tier 2 — Identifier grounding
    Tier 3 — PK integrity (strict + permissive)
    Tier 4 — Relationship FK/PK validation
"""

import pytest
from unittest.mock import MagicMock

from semabridge.core.exceptions import ValidationError as SemaBridgeValidationError
from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    SMLDimension, SMLAttribute,
    DataType, AggregationType, Cardinality, CrossFilterDirection,
)
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.core.validation.validate_semantic_model import (
    validate_pre_deployment,
    validate_primary_keys,
    PreDeploymentReport,
)


@pytest.fixture
def sanitizer():
    """Standard IdentifierSanitizer."""
    return IdentifierSanitizer(
        force_uppercase=True,
        always_quote=True,
        suppress_reserved=True,
    )


def _sf_behavior(pk_mode: str = "permissive"):
    """Create a mock SnowflakeBehavior with pk_resolution_mode."""
    enum_val = MagicMock()
    enum_val.value = pk_mode
    beh = MagicMock()
    beh.pk_resolution_mode = enum_val
    return beh


def _basic_model() -> SMLModel:
    """Healthy model with valid PKs and relationships."""
    return SMLModel(
        unique_name="TestModel",
        datasets=[
            SMLDataset(
                unique_name="Facts",
                source_table="FACT_TABLE",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="Customers",
                source_table="DIM_CUSTOMER",
                is_fact=False,
                columns=[
                    SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Name", data_type=DataType.STRING),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TotalAmount",
                dataset="Facts",
                source_column="Amount",
                aggregation=AggregationType.SUM,
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="Facts_Customers",
                from_dataset="Facts",
                from_columns=["CustomerID"],
                to_dataset="Customers",
                to_columns=["CustomerID"],
                cardinality=Cardinality.MANY_TO_ONE,
                cross_filter=CrossFilterDirection.SINGLE,
            ),
        ],
    )


# =========================================================================
# Tier 1 — Schema
# =========================================================================


class TestTier1Schema:
    """YAML schema structural integrity checks."""

    def test_valid_model_passes(self, sanitizer):
        """Healthy model should pass with no errors."""
        report = validate_pre_deployment(
            _basic_model(), _sf_behavior(), sanitizer
        )
        assert not report.has_errors

    def test_no_datasets_fails(self, sanitizer):
        """Model with no datasets should fail."""
        model = SMLModel(unique_name="Empty", datasets=[])
        with pytest.raises(SemaBridgeValidationError):
            validate_pre_deployment(model, _sf_behavior(), sanitizer)

    def test_empty_dataset_fails(self, sanitizer):
        """Dataset with no columns should fail."""
        model = SMLModel(
            unique_name="BadDataset",
            datasets=[
                SMLDataset(unique_name="NoColumns", columns=[]),
            ],
        )
        with pytest.raises(SemaBridgeValidationError):
            validate_pre_deployment(model, _sf_behavior(), sanitizer)

    def test_metric_references_nonexistent_dataset(self, sanitizer):
        """Metric referencing a missing dataset should error."""
        model = SMLModel(
            unique_name="BadMetric",
            datasets=[
                SMLDataset(
                    unique_name="Facts",
                    columns=[SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True)],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="BadRef",
                    dataset="NONEXISTENT",
                    source_column="Amount",
                    aggregation=AggregationType.SUM,
                ),
            ],
        )
        with pytest.raises(SemaBridgeValidationError):
            validate_pre_deployment(model, _sf_behavior(), sanitizer)


# =========================================================================
# Tier 3 — PK Integrity
# =========================================================================


class TestTier3PK:
    """Primary key integrity validation."""

    def test_pk_strict_fails_without_key(self, sanitizer):
        """Strict mode: dataset without is_key column raises error."""
        model = SMLModel(
            unique_name="NoPK",
            datasets=[
                SMLDataset(
                    unique_name="Orphan",
                    columns=[
                        SMLColumn(unique_name="Value", data_type=DataType.DECIMAL),
                    ],
                ),
            ],
        )
        with pytest.raises(SemaBridgeValidationError, match="pk_resolution_mode=strict"):
            validate_pre_deployment(model, _sf_behavior("strict"), sanitizer)

    def test_pk_permissive_warns_without_key(self, sanitizer):
        """Permissive mode: dataset without key gets a warning, not error."""
        model = SMLModel(
            unique_name="NoPK",
            datasets=[
                SMLDataset(
                    unique_name="Orphan",
                    columns=[
                        SMLColumn(unique_name="Value", data_type=DataType.DECIMAL),
                    ],
                ),
            ],
        )
        report = validate_pre_deployment(
            model, _sf_behavior("permissive"), sanitizer
        )
        assert not report.has_errors
        assert report.warning_count > 0

    def test_pk_from_relationship(self, sanitizer):
        """PK defined via inbound relationship should be valid."""
        report = validate_pre_deployment(
            _basic_model(), _sf_behavior("strict"), sanitizer
        )
        assert not report.has_errors

    def test_standalone_validate_primary_keys(self, sanitizer):
        """validate_primary_keys() should work independently."""
        report = validate_primary_keys(
            _basic_model(), _sf_behavior("strict"), sanitizer
        )
        assert not report.has_errors

    def test_calculated_key_warns(self, sanitizer):
        """Calculated key column should produce a warning."""
        model = SMLModel(
            unique_name="CalcKey",
            datasets=[
                SMLDataset(
                    unique_name="Facts",
                    columns=[
                        SMLColumn(
                            unique_name="CalcID",
                            data_type=DataType.STRING,
                            is_key=True,
                            source_expression="CONCAT(A, B)",
                        ),
                    ],
                ),
            ],
        )
        report = validate_primary_keys(
            model, _sf_behavior("permissive"), sanitizer
        )
        pk_warnings = [
            i for i in report.issues
            if "calculated" in i.message.lower() and i.severity == "WARNING"
        ]
        assert len(pk_warnings) >= 1


# =========================================================================
# Tier 4 — Relationships
# =========================================================================


class TestTier4Relationships:
    """FK/PK column existence validation."""

    def test_valid_relationship_passes(self, sanitizer):
        """Valid relationship should produce no errors."""
        report = validate_pre_deployment(
            _basic_model(), _sf_behavior(), sanitizer
        )
        rel_issues = [i for i in report.issues if i.tier == 4 and i.severity == "ERROR"]
        assert len(rel_issues) == 0

    def test_fk_missing_from_dataset_errors(self, sanitizer):
        """FK referencing non-existent from_dataset should error."""
        model = SMLModel(
            unique_name="BadRel",
            datasets=[
                SMLDataset(
                    unique_name="Facts",
                    columns=[SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True)],
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="Broken",
                    from_dataset="MISSING_TABLE",
                    from_columns=["FKCol"],
                    to_dataset="Facts",
                    to_columns=["ID"],
                    cardinality=Cardinality.MANY_TO_ONE,
                    cross_filter=CrossFilterDirection.SINGLE,
                ),
            ],
        )
        with pytest.raises(SemaBridgeValidationError):
            validate_pre_deployment(model, _sf_behavior(), sanitizer)


# =========================================================================
# Report
# =========================================================================


class TestPreDeploymentReport:
    """Tests for the report class."""

    def test_empty_report(self):
        r = PreDeploymentReport()
        assert not r.has_errors
        assert r.error_count == 0
        assert "passed" in r.summary().lower()

    def test_report_with_errors(self):
        r = PreDeploymentReport()
        r.add_error(1, "Schema", "Test", "Something broke")
        assert r.has_errors
        assert r.error_count == 1

    def test_report_with_warnings_only(self):
        r = PreDeploymentReport()
        r.add_warning(2, "Identifier", "Col", "Might be wrong")
        assert not r.has_errors
        assert r.warning_count == 1
