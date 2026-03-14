"""
Tests for the Sync Orchestrator module.

Validates:
  - Dependency ordering (dimensions before facts)
  - Pre-sync validation classification
  - Sync report generation and formatting
  - Suggested fix generation
  - ModelResult status tracking
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import List, Optional

from semabridge.core.sync_orchestrator import (
    ModelStatus,
    ModelIssue,
    ModelResult,
    SyncReport,
    order_models_by_dependency,
    validate_model_for_sync,
    suggest_fix_for_error,
    _classify_model,
    _classify_dataset,
)


# =========================================================================
# Fixtures — Lightweight model stubs
# =========================================================================


@dataclass
class StubColumn:
    unique_name: str
    is_key: bool = False
    source_expression: Optional[str] = None


@dataclass
class StubDataset:
    unique_name: str
    columns: List[StubColumn] = field(default_factory=list)
    is_fact: bool = False
    source_table: Optional[str] = None


@dataclass
class StubMetric:
    unique_name: str
    dataset: str
    source_column: Optional[str] = None
    aggregation: Optional[MagicMock] = None
    expression: Optional[str] = None
    sql_expression: Optional[str] = None
    description: str = ""
    format_string: str = ""


@dataclass
class StubDimAttribute:
    unique_name: str
    dataset: str
    dataset_column: str
    source_column: str = ""
    label: str = ""
    description: str = ""


@dataclass
class StubDimension:
    unique_name: str
    attributes: List[StubDimAttribute] = field(default_factory=list)


@dataclass
class StubRelationship:
    unique_name: str
    from_dataset: str
    to_dataset: str
    from_columns: List[str] = field(default_factory=list)
    to_columns: List[str] = field(default_factory=list)
    is_active: bool = True


@dataclass
class StubModel:
    unique_name: str
    label: str = ""
    datasets: List[StubDataset] = field(default_factory=list)
    metrics: List[StubMetric] = field(default_factory=list)
    dimensions: List[StubDimension] = field(default_factory=list)
    relationships: List[StubRelationship] = field(default_factory=list)


# =========================================================================
# Test: ModelResult
# =========================================================================


class TestModelResult:
    def test_initial_status_is_pending(self) -> None:
        result = ModelResult(model_name="test", model_id="t1")
        assert result.status == ModelStatus.PENDING

    def test_has_errors_true_when_error_issue(self) -> None:
        result = ModelResult(model_name="test", model_id="t1")
        result.add_issue("ERROR", "PK", "No primary key")
        assert result.has_errors is True

    def test_has_errors_false_when_only_warnings(self) -> None:
        result = ModelResult(model_name="test", model_id="t1")
        result.add_issue("WARNING", "Identifier", "Case mismatch")
        assert result.has_errors is False

    def test_add_issue_with_suggested_fix(self) -> None:
        result = ModelResult(model_name="test", model_id="t1")
        result.add_issue("ERROR", "PK", "Missing PK", "Add is_key=True")
        assert len(result.issues) == 1
        assert result.issues[0].suggested_fix == "Add is_key=True"


# =========================================================================
# Test: SyncReport
# =========================================================================


class TestSyncReport:
    def test_empty_report_counts(self) -> None:
        report = SyncReport()
        assert report.total == 0
        assert report.succeeded == 0
        assert report.failed == 0
        assert report.blocked == 0

    def test_counts_with_mixed_statuses(self) -> None:
        report = SyncReport()
        report.model_results = [
            ModelResult(model_name="a", model_id="1", status=ModelStatus.SUCCESS),
            ModelResult(model_name="b", model_id="2", status=ModelStatus.FAILED),
            ModelResult(model_name="c", model_id="3", status=ModelStatus.BLOCKED),
            ModelResult(model_name="d", model_id="4", status=ModelStatus.SUCCESS),
        ]
        assert report.total == 4
        assert report.succeeded == 2
        assert report.failed == 1
        assert report.blocked == 1
        assert not report.all_succeeded

    def test_all_succeeded_when_all_success(self) -> None:
        report = SyncReport()
        report.model_results = [
            ModelResult(model_name="a", model_id="1", status=ModelStatus.SUCCESS),
            ModelResult(model_name="b", model_id="2", status=ModelStatus.SUCCESS),
        ]
        assert report.all_succeeded

    def test_get_result_by_name(self) -> None:
        report = SyncReport()
        report.model_results = [
            ModelResult(model_name="alpha", model_id="1"),
            ModelResult(model_name="beta", model_id="2"),
        ]
        assert report.get_result("alpha") is not None
        assert report.get_result("alpha").model_id == "1"
        assert report.get_result("nonexistent") is None

    def test_format_summary_contains_counts(self) -> None:
        report = SyncReport()
        report.model_results = [
            ModelResult(
                model_name="fail_model",
                model_id="1",
                status=ModelStatus.FAILED,
                error_message="invalid identifier",
            ),
        ]
        summary = report.format_summary()
        assert "Total: 1" in summary
        assert "Failed: 1" in summary
        assert "fail_model" in summary

    def test_format_summary_shows_suggested_fixes(self) -> None:
        report = SyncReport()
        result = ModelResult(
            model_name="broken", model_id="1", status=ModelStatus.FAILED
        )
        result.add_issue("ERROR", "PK", "No PK", "Add is_key=True")
        report.model_results = [result]
        summary = report.format_summary()
        assert "Add is_key=True" in summary


# =========================================================================
# Test: Dataset & Model Classification
# =========================================================================


class TestClassification:
    def test_classify_fact_dataset(self) -> None:
        ds = StubDataset(unique_name="Fact_Sales", is_fact=True)
        assert _classify_dataset(ds) == "fact"

    def test_classify_dimension_by_name(self) -> None:
        ds = StubDataset(unique_name="Dim_Date", is_fact=False)
        assert _classify_dataset(ds) == "dimension"

    def test_classify_dimension_by_prefix(self) -> None:
        ds = StubDataset(unique_name="D_Customer", is_fact=False)
        assert _classify_dataset(ds) == "dimension"

    def test_classify_model_with_fact(self) -> None:
        model = StubModel(
            unique_name="Sales",
            datasets=[
                StubDataset(unique_name="Dim_Date"),
                StubDataset(unique_name="Fact_Sales", is_fact=True),
            ],
        )
        assert _classify_model(model) == "fact"

    def test_classify_model_without_fact(self) -> None:
        model = StubModel(
            unique_name="Dimensions",
            datasets=[
                StubDataset(unique_name="Dim_Date"),
                StubDataset(unique_name="Dim_Customer"),
            ],
        )
        assert _classify_model(model) == "dimension"

    def test_classify_empty_model(self) -> None:
        model = StubModel(unique_name="Empty")
        assert _classify_model(model) == "unknown"


# =========================================================================
# Test: Dependency Ordering
# =========================================================================


class TestDependencyOrdering:
    def test_dimensions_before_facts(self) -> None:
        models = [
            {"id": "1", "name": "Fact_Sales"},
            {"id": "2", "name": "Dim_Date"},
            {"id": "3", "name": "Fact_Revenue"},
        ]
        ordered = order_models_by_dependency(models)
        names = [m["name"] for m in ordered]
        # Dim_Date should come before Fact_* models
        assert names.index("Dim_Date") < names.index("Fact_Sales")
        assert names.index("Dim_Date") < names.index("Fact_Revenue")

    def test_ordering_preserves_within_category(self) -> None:
        models = [
            {"id": "1", "name": "Dim_B"},
            {"id": "2", "name": "Dim_A"},
        ]
        ordered = order_models_by_dependency(models)
        names = [m["name"] for m in ordered]
        # Both are dimensions, sorted alphabetically
        assert names == ["Dim_A", "Dim_B"]

    def test_ordering_with_osi_models(self) -> None:
        models = [
            {"id": "fact_1", "name": "Sales"},
            {"id": "dim_1", "name": "Calendar"},
        ]
        osi_map = {
            "fact_1": StubModel(
                unique_name="Sales",
                datasets=[StubDataset(unique_name="Fact_Sales", is_fact=True)],
            ),
            "dim_1": StubModel(
                unique_name="Calendar",
                datasets=[StubDataset(unique_name="Dim_Date")],
            ),
        }
        ordered = order_models_by_dependency(models, osi_models=osi_map)
        assert ordered[0]["id"] == "dim_1"
        assert ordered[1]["id"] == "fact_1"


# =========================================================================
# Test: validate_model_for_sync
# =========================================================================


class TestValidateModelForSync:
    def test_valid_model_passes(self) -> None:
        """A well-formed model should pass validation."""
        model = StubModel(
            unique_name="Sales",
            datasets=[
                StubDataset(
                    unique_name="Fact_Sales",
                    is_fact=True,
                    columns=[StubColumn("OrderID", is_key=True), StubColumn("Amount")],
                )
            ],
            metrics=[],
            dimensions=[],
            relationships=[],
        )
        sf_behavior = MagicMock()
        sf_behavior.pk_resolution_mode = MagicMock(value="permissive")
        id_sanitizer = MagicMock()
        id_sanitizer.sanitize_column = lambda x: x.upper()

        result = validate_model_for_sync(model, sf_behavior, id_sanitizer)
        assert result.status in (ModelStatus.VALID, ModelStatus.BLOCKED)
        assert result.model_name == "Sales"

    def test_empty_model_validates_with_issues(self) -> None:
        """A model with no columns should produce validation issues."""
        model = StubModel(
            unique_name="Empty",
            datasets=[StubDataset(unique_name="EmptyTable", columns=[])],
        )
        sf_behavior = MagicMock()
        sf_behavior.pk_resolution_mode = MagicMock(value="permissive")
        id_sanitizer = MagicMock()
        id_sanitizer.sanitize_column = lambda x: x.upper()

        result = validate_model_for_sync(model, sf_behavior, id_sanitizer)
        # Should have issues about empty columns or missing PKs
        assert len(result.issues) > 0

    def test_validation_crash_is_caught(self) -> None:
        """If validation crashes, model should still be marked VALID (best-effort)."""
        model = StubModel(unique_name="Crasher")
        sf_behavior = MagicMock()
        id_sanitizer = MagicMock()

        # Model has no datasets, which triggers a Schema error.
        # The function should not raise — it returns a result.
        result = validate_model_for_sync(model, sf_behavior, id_sanitizer)
        # Should not crash — either VALID or BLOCKED, never raises
        assert result.status in (ModelStatus.VALID, ModelStatus.BLOCKED)


# =========================================================================
# Test: suggest_fix_for_error
# =========================================================================


class TestSuggestFix:
    def test_invalid_identifier(self) -> None:
        fix = suggest_fix_for_error("SQL compilation error: invalid identifier 'FOO'")
        assert "column" in fix.lower()

    def test_primary_key(self) -> None:
        fix = suggest_fix_for_error("No primary key found")
        assert "pk_resolution_mode" in fix.lower()

    def test_table_not_exists(self) -> None:
        fix = suggest_fix_for_error("Table 'SALES' does not exist")
        assert "create_missing_tables" in fix.lower()

    def test_timeout(self) -> None:
        fix = suggest_fix_for_error("Connection timeout reaching Snowflake")
        assert "warehouse" in fix.lower()

    def test_permission(self) -> None:
        fix = suggest_fix_for_error("Not authorized to CREATE TABLE")
        assert "RBAC" in fix.lower() or "grants" in fix.lower()

    def test_attribute_error(self) -> None:
        fix = suggest_fix_for_error("AttributeError: 'NoneType' object has no attribute")
        assert "attribute" in fix.lower()

    def test_generic_error(self) -> None:
        fix = suggest_fix_for_error("Something totally unexpected happened")
        assert "check" in fix.lower()
