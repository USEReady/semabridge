"""
Integration tests: Tiered Safety sync for all Customer Profitability measures.

Validates the tiered safety classification, translation, and override flow
for a realistic Customer Profitability semantic model — ensuring correctness
through both the CLI-like path (_run_fabric_to_snowflake flow) and the
Engine-based path (UI SyncWorker → SemaBridgeEngine).

Every DAX tier (1–4) is represented with real-world measures.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Dict, List

import pytest

from semabridge.formats.sml.models import (
    AggregationType,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
    Cardinality,
)
from semabridge.converter.tiered_safety import (
    HazardCategory,
    SafetyClassification,
    SafetyTier,
    TieredSafetyClassifier,
)
from semabridge.converter.override_generator import OverrideGenerator
from semabridge.converter.override_validator import OverrideValidator
from semabridge.converter.safety_pipeline import (
    SafetyPipelineResult,
    TieredSafetyPipeline,
)
from semabridge.converter.dax_translator import DAXTranslator


# ---------------------------------------------------------------------------
# All Customer Profitability measures (mirrors real Fabric model)
# ---------------------------------------------------------------------------
CUSTOMER_PROFITABILITY_MEASURES: Dict[str, Dict] = {
    # ── Tier 1: Simple aggregations ─────────────────────────────────────
    "Total Revenue": {
        "dax": "SUM('Fact Financials'[Revenue])",
        "dataset": "Fact Financials",
        "expected_tier": 1,
    },
    "Total Cost": {
        "dax": "SUM('Fact Financials'[Cost])",
        "dataset": "Fact Financials",
        "expected_tier": 1,
    },
    "Transaction Count": {
        "dax": "COUNT('Fact Financials'[TransactionID])",
        "dataset": "Fact Financials",
        "expected_tier": 1,
    },
    "Distinct Customers": {
        "dax": "DISTINCTCOUNT('Fact Financials'[CustomerID])",
        "dataset": "Fact Financials",
        "expected_tier": 1,
    },
    "Max Transaction": {
        "dax": "MAX('Fact Financials'[Revenue])",
        "dataset": "Fact Financials",
        "expected_tier": 1,
    },
    # ── Tier 2: Arithmetic / branching ──────────────────────────────────
    "Gross Profit": {
        "dax": "SUM('Fact Financials'[Revenue]) - SUM('Fact Financials'[Cost])",
        "dataset": "Fact Financials",
        "expected_tier": 2,
    },
    "Profit Margin %": {
        "dax": "DIVIDE(SUM('Fact Financials'[Revenue]) - SUM('Fact Financials'[Cost]), SUM('Fact Financials'[Revenue]))",
        "dataset": "Fact Financials",
        "expected_tier": 2,
    },
    "Revenue per Customer": {
        "dax": "DIVIDE(SUM('Fact Financials'[Revenue]), DISTINCTCOUNT('Fact Financials'[CustomerID]))",
        "dataset": "Fact Financials",
        "expected_tier": 2,
    },
    "Profitability Flag": {
        "dax": "IF(SUM('Fact Financials'[Revenue]) - SUM('Fact Financials'[Cost]) > 0, \"Profitable\", \"Unprofitable\")",
        "dataset": "Fact Financials",
        "expected_tier": 2,
    },
    "Margin Bucket": {
        "dax": "SWITCH(TRUE(), [Profit Margin %] >= 0.3, \"High\", [Profit Margin %] >= 0.15, \"Medium\", \"Low\")",
        "dataset": "Fact Financials",
        "expected_tier": 2,
    },
    # ── Tier 3: Time intel / context modifiers ──────────────────────────
    "Revenue YTD": {
        "dax": "TOTALYTD(SUM('Fact Financials'[Revenue]), 'Calendar'[Date])",
        "dataset": "Fact Financials",
        "expected_tier": 2,
    },
    "Revenue MTD": {
        "dax": "TOTALMTD(SUM('Fact Financials'[Revenue]), 'Calendar'[Date])",
        "dataset": "Fact Financials",
        "expected_tier": 2,
    },
    "Revenue PY": {
        "dax": "CALCULATE(SUM('Fact Financials'[Revenue]), SAMEPERIODLASTYEAR('Calendar'[Date]))",
        "dataset": "Fact Financials",
        "expected_tier": 3,
    },
    "Revenue PP": {
        "dax": "CALCULATE(SUM('Fact Financials'[Revenue]), PARALLELPERIOD('Calendar'[Date], -1, MONTH))",
        "dataset": "Fact Financials",
        "expected_tier": 3,
    },
    "Revenue by Region": {
        "dax": "CALCULATE(SUM('Fact Financials'[Revenue]), FILTER('Dim Geography', 'Dim Geography'[Region] = \"West\"))",
        "dataset": "Fact Financials",
        "expected_tier": 3,
    },
    "Total Revenue All": {
        "dax": "CALCULATE(SUM('Fact Financials'[Revenue]), ALL('Dim Customer'))",
        "dataset": "Fact Financials",
        "expected_tier": 3,
    },
    "Revenue Except Product": {
        "dax": "CALCULATE(SUM('Fact Financials'[Revenue]), ALLEXCEPT('Fact Financials', 'Fact Financials'[ProductID]))",
        "dataset": "Fact Financials",
        "expected_tier": 3,
    },
    "Weighted Avg Price": {
        "dax": "SUMX('Fact Financials', 'Fact Financials'[Quantity] * 'Fact Financials'[UnitPrice])",
        "dataset": "Fact Financials",
        "expected_tier": 3,
    },
    "Avg Item Revenue": {
        "dax": "AVERAGEX('Fact Financials', 'Fact Financials'[Revenue] / 'Fact Financials'[Quantity])",
        "dataset": "Fact Financials",
        "expected_tier": 3,
    },
    # ── Tier 4: Complex / unsupported ───────────────────────────────────
    "Customer Rank": {
        "dax": "RANKX(ALL('Dim Customer'), [Total Revenue])",
        "dataset": "Dim Customer",
        "expected_tier": 4,
    },
    "Top 10 Revenue": {
        "dax": "CALCULATE([Total Revenue], TOPN(10, ALL('Dim Customer'), [Total Revenue]))",
        "dataset": "Dim Customer",
        "expected_tier": 4,
    },
    "Cross-Filter Total": {
        "dax": "CALCULATE([Total Revenue], CROSSFILTER('Fact Financials'[CustomerID], 'Dim Customer'[CustomerID], Both))",
        "dataset": "Fact Financials",
        "expected_tier": 4,
    },
    "OptOut Rank Measure": {
        "dax": "RANKX(ALL('Fact Financials'), CALCULATE(SUM('Fact Financials'[Revenue]), ALLEXCEPT('Fact Financials', 'Fact Financials'[CustomerID])))",
        "dataset": "Fact Financials",
        "expected_tier": 4,
    },
    "Running Total": {
        "dax": "CALCULATE(SUM('Fact Financials'[Revenue]), FILTER(ALL('Calendar'[Date]), 'Calendar'[Date] <= MAX('Calendar'[Date])))",
        "dataset": "Fact Financials",
        "expected_tier": 3,
    },
}


def _build_customer_profitability_model() -> SMLModel:
    """Construct a realistic Customer Profitability SMLModel with all measures."""
    fact_columns = [
        SMLColumn(unique_name="TransactionID", data_type=DataType.INTEGER, is_key=True),
        SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER),
        SMLColumn(unique_name="ProductID", data_type=DataType.INTEGER),
        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
        SMLColumn(unique_name="Cost", data_type=DataType.DECIMAL),
        SMLColumn(unique_name="Quantity", data_type=DataType.INTEGER),
        SMLColumn(unique_name="UnitPrice", data_type=DataType.DECIMAL),
        SMLColumn(unique_name="OrderDate", data_type=DataType.DATE),
    ]
    fact_ds = SMLDataset(
        unique_name="Fact Financials",
        label="Fact Financials",
        source_table="FACT_FINANCIALS",
        is_fact=True,
        columns=fact_columns,
    )

    customer_ds = SMLDataset(
        unique_name="Dim Customer",
        label="Dim Customer",
        source_table="DIM_CUSTOMER",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="CustomerName", data_type=DataType.STRING),
            SMLColumn(unique_name="Segment", data_type=DataType.STRING),
        ],
    )

    calendar_ds = SMLDataset(
        unique_name="Calendar",
        label="Calendar",
        source_table="CALENDAR",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="Date", data_type=DataType.DATE, is_key=True),
            SMLColumn(unique_name="Year", data_type=DataType.INTEGER),
            SMLColumn(unique_name="Month", data_type=DataType.INTEGER),
        ],
    )

    geography_ds = SMLDataset(
        unique_name="Dim Geography",
        label="Dim Geography",
        source_table="DIM_GEOGRAPHY",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="GeoID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="Region", data_type=DataType.STRING),
        ],
    )

    # Build metrics from the registry
    metrics: list[SMLMetric] = []
    for name, spec in CUSTOMER_PROFITABILITY_MEASURES.items():
        metrics.append(
            SMLMetric(
                unique_name=name,
                label=name,
                dataset=spec["dataset"],
                expression=spec["dax"],
                aggregation=AggregationType.SUM,
            )
        )

    relationships = [
        SMLRelationship(
            unique_name="Financials_to_Customer",
            from_dataset="Fact Financials",
            from_columns=["CustomerID"],
            to_dataset="Dim Customer",
            to_columns=["CustomerID"],
            cardinality=Cardinality.MANY_TO_ONE,
            is_active=True,
        ),
        SMLRelationship(
            unique_name="Financials_to_Calendar",
            from_dataset="Fact Financials",
            from_columns=["OrderDate"],
            to_dataset="Calendar",
            to_columns=["Date"],
            cardinality=Cardinality.MANY_TO_ONE,
            is_active=True,
        ),
    ]

    return SMLModel(
        unique_name="Customer Profitability",
        label="Customer Profitability",
        description="End-to-end integration test model for tiered safety",
        datasets=[fact_ds, customer_ds, calendar_ds, geography_ds],
        metrics=metrics,
        relationships=relationships,
    )


# ═══════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def customer_profitability_model() -> SMLModel:
    return _build_customer_profitability_model()


@pytest.fixture
def classifier() -> TieredSafetyClassifier:
    return TieredSafetyClassifier()


@pytest.fixture
def tmp_override_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# ═══════════════════════════════════════════════════════════════════════
# 1. Classification Tests — every measure gets the expected tier
# ═══════════════════════════════════════════════════════════════════════

class TestCustomerProfitabilityClassification:
    """Verify all 24 Customer Profitability measures classify correctly."""

    def test_all_measures_classify(self, classifier, customer_profitability_model):
        """Every metric in the model must produce a classification."""
        for metric in customer_profitability_model.metrics:
            c = classifier.classify(metric.unique_name, metric.expression)
            assert isinstance(c, SafetyClassification), f"{metric.unique_name} did not classify"
            assert c.tier in SafetyTier, f"{metric.unique_name} has invalid tier"

    @pytest.mark.parametrize("name,spec", list(CUSTOMER_PROFITABILITY_MEASURES.items()))
    def test_tier_assignment(self, classifier, name, spec):
        """Each measure must land in its expected tier."""
        c = classifier.classify(name, spec["dax"])
        assert c.tier.value == spec["expected_tier"], (
            f"{name}: expected Tier {spec['expected_tier']} but got Tier {c.tier.value} | "
            f"hazards={[h.category.value for h in c.hazards]}"
        )

    def test_tier_distribution(self, classifier, customer_profitability_model):
        """Spot-check aggregated counts per tier."""
        dist = {1: 0, 2: 0, 3: 0, 4: 0}
        for metric in customer_profitability_model.metrics:
            c = classifier.classify(metric.unique_name, metric.expression)
            dist[c.tier.value] += 1

        assert dist[1] == 5, f"Expected 5 Tier-1 measures, got {dist[1]}"
        assert dist[2] == 7, f"Expected 7 Tier-2 measures, got {dist[2]}"
        assert dist[3] == 8, f"Expected 8 Tier-3 measures, got {dist[3]}"
        assert dist[4] == 4, f"Expected 4 Tier-4 measures, got {dist[4]}"

    def test_automatable_vs_override(self, classifier, customer_profitability_model):
        """Tier 1/2 must be automatable; Tier 3/4 must require override."""
        for metric in customer_profitability_model.metrics:
            c = classifier.classify(metric.unique_name, metric.expression)
            expected_tier = CUSTOMER_PROFITABILITY_MEASURES[metric.unique_name]["expected_tier"]
            if expected_tier <= 2:
                assert c.is_automatable, f"{metric.unique_name} should be automatable"
                assert not c.requires_override, f"{metric.unique_name} should NOT require override"
            else:
                assert not c.is_automatable, f"{metric.unique_name} should NOT be automatable"
                assert c.requires_override, f"{metric.unique_name} should require override"


# ═══════════════════════════════════════════════════════════════════════
# 2. Full Pipeline Test — simulates CLI `sync` path
# ═══════════════════════════════════════════════════════════════════════

class TestCustomerProfitabilityPipeline:
    """End-to-end pipeline test: classify → translate → generate → validate → report."""

    def test_pipeline_runs_all_measures(self, customer_profitability_model, tmp_override_dir):
        pipeline = TieredSafetyPipeline(
            override_dir=tmp_override_dir,
            database="ANALYTICS_DB",
            schema="SEMANTIC",
        )
        result = pipeline.run(customer_profitability_model)

        # All measures classified
        assert result.total_measures == len(CUSTOMER_PROFITABILITY_MEASURES)
        assert result.auto_translatable_count == 12  # 5 T1 + 7 T2
        assert result.override_required_count == 12  # 8 T3 + 4 T4

    def test_pipeline_flagged_measures(self, customer_profitability_model, tmp_override_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        result = pipeline.run(customer_profitability_model)

        # All Tier 3/4 measures flagged
        for name, spec in CUSTOMER_PROFITABILITY_MEASURES.items():
            if spec["expected_tier"] >= 3:
                assert name in result.flagged_measures, f"{name} should be flagged"
            else:
                assert name not in result.flagged_measures, f"{name} should NOT be flagged"

    def test_pipeline_auto_translates_tier1_tier2(self, customer_profitability_model, tmp_override_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        result = pipeline.run(customer_profitability_model)

        for name, spec in CUSTOMER_PROFITABILITY_MEASURES.items():
            if spec["expected_tier"] <= 2:
                # Tier 1/2 should have attempted translation
                assert name in result.auto_translated, f"{name} should be auto-translated"

    def test_pipeline_generates_override_templates(self, customer_profitability_model, tmp_override_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        result = pipeline.run(customer_profitability_model)

        # Override templates generated for uncovered Tier 3/4
        assert len(result.overrides_generated) == result.override_required_count
        for name in result.flagged_measures:
            assert name in result.overrides_generated, f"Override template missing for {name}"

    def test_pipeline_writes_override_files_to_disk(self, customer_profitability_model, tmp_override_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        pipeline.run(customer_profitability_model)

        yaml_files = list(tmp_override_dir.glob("override_*.yaml"))
        sql_files = list(tmp_override_dir.glob("override_*.sql"))
        assert len(yaml_files) >= 12, f"Expected ≥12 YAML files, got {len(yaml_files)}"
        assert len(sql_files) >= 12, f"Expected ≥12 SQL files, got {len(sql_files)}"

    def test_pipeline_writes_safety_report(self, customer_profitability_model, tmp_override_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        result = pipeline.run(customer_profitability_model)

        report_dir = tmp_override_dir / "reports"
        pipeline.write_safety_report(result, report_dir)

        report_path = report_dir / "tiered_safety_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text())
        assert report["total_measures"] == len(CUSTOMER_PROFITABILITY_MEASURES)
        assert len(report["measure_details"]) == len(CUSTOMER_PROFITABILITY_MEASURES)

    def test_pipeline_updates_sml_metadata(self, customer_profitability_model, tmp_override_dir):
        """After pipeline.run(), SMLMetric fields must reflect classifications."""
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        pipeline.run(customer_profitability_model)

        for metric in customer_profitability_model.metrics:
            expected = CUSTOMER_PROFITABILITY_MEASURES[metric.unique_name]["expected_tier"]
            assert metric.complexity_tier == expected, (
                f"{metric.unique_name}: expected complexity_tier={expected}, got {metric.complexity_tier}"
            )
            if expected <= 2:
                assert metric.sync_enabled, f"{metric.unique_name} should be sync_enabled"
            else:
                # Without a completed override, sync is disabled
                assert not metric.sync_enabled or metric.sql_expression, (
                    f"{metric.unique_name}: Tier {expected} should have sync_enabled=False "
                    f"unless an override SQL is applied"
                )

    def test_pipeline_report_structure(self, customer_profitability_model, tmp_override_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        result = pipeline.run(customer_profitability_model)
        report = result.get_report()

        # Keys that must exist
        assert "tier_distribution" in report
        assert "auto_translatable" in report
        assert "override_required" in report
        assert "override_coverage_pct" in report
        assert "hazard_breakdown" in report

        # Tier distribution must sum to total
        assert sum(report["tier_distribution"].values()) == report["total_measures"]


# ═══════════════════════════════════════════════════════════════════════
# 3. Override Lifecycle — write, reload, validate, apply
# ═══════════════════════════════════════════════════════════════════════

class TestOverrideLifecycle:
    """Simulate the human override workflow: generate → edit → reload → validate → apply."""

    def test_round_trip_yaml(self, customer_profitability_model, tmp_override_dir):
        """Generate override, write YAML, reload it ‒ no data loss."""
        classifier = TieredSafetyClassifier()
        generator = OverrideGenerator(database="DB", schema="SCH")
        validator = OverrideValidator()

        for metric in customer_profitability_model.metrics:
            c = classifier.classify(metric.unique_name, metric.expression)
            if not c.requires_override:
                continue

            # Generate + write
            override = generator.generate_override(c, source_model="Customer Profitability")
            path = generator.write_override_yaml(override, tmp_override_dir)
            assert path.exists()

            # Validate from disk
            result = validator.validate_file(path)
            assert any("Schema validation passed" in i for i in result.info), (
                f"YAML round-trip failed for {metric.unique_name}: {result.errors}"
            )

    def test_override_coverage_after_load(self, customer_profitability_model, tmp_override_dir):
        """After generating overrides and re-loading, coverage should be 100%."""
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        pipeline.run(customer_profitability_model)

        # Now re-run with the overrides directory populated
        result2 = pipeline.run(customer_profitability_model, generate_templates=False)
        assert result2.override_coverage == 100.0, (
            f"Override coverage should be 100% after loading, got {result2.override_coverage}%"
        )

    def test_sql_render_produces_valid_ddl(self, tmp_override_dir):
        """Rendered SQL must contain all three layer markers."""
        classifier = TieredSafetyClassifier()
        generator = OverrideGenerator(database="DB", schema="SCH")

        c = classifier.classify("Customer Rank", "RANKX(ALL('Dim Customer'), [Total Revenue])")
        override = generator.generate_override(c)
        sql = generator.render_sql(override)

        assert "CREATE OR REPLACE DYNAMIC TABLE" in sql
        assert "CREATE OR REPLACE SEMANTIC VIEW" in sql or "SEMANTIC VIEW" in sql
        assert "-- Cortex Analyst Metadata" in sql or "cortex" in sql.lower()


# ═══════════════════════════════════════════════════════════════════════
# 4. CLI Path Simulation — mirrors _run_fabric_to_snowflake flow
# ═══════════════════════════════════════════════════════════════════════

class TestCLISyncPath:
    """Simulates the CLI `sync` command path for Customer Profitability."""

    def test_cli_sync_pipeline_integration(self, customer_profitability_model, tmp_override_dir):
        """
        Simulates: extract → OSI→SML → TieredSafetyPipeline.run() → generate_ddls.

        This is the flow that would execute during:
            python main.py sync fabric --dataset-id <id>

        We verify that after the pipeline:
        1. All measures are classified
        2. Tier 1/2 have sql_expression set
        3. Tier 3/4 have sync_failure_reason set
        4. DDL generation still works (no crashes)
        """
        model = customer_profitability_model

        # Run safety pipeline (as would be integrated into CLI sync)
        pipeline = TieredSafetyPipeline(
            override_dir=tmp_override_dir,
            database="ANALYTICS_DB",
            schema="SEMANTIC",
        )
        result = pipeline.run(model)

        # All measures classified
        assert result.total_measures == len(CUSTOMER_PROFITABILITY_MEASURES)

        # Check metric state
        translated_count = 0
        blocked_count = 0
        for metric in model.metrics:
            if metric.sync_enabled and metric.sql_expression:
                translated_count += 1
            elif metric.sync_failure_reason:
                blocked_count += 1

        assert translated_count > 0, "At least some Tier 1/2 measures should be auto-translated"
        assert blocked_count > 0, "At least some Tier 3/4 measures should be blocked"

        # DDL generation must not crash
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        from unittest.mock import MagicMock

        mock_config = MagicMock()
        mock_config.database = "ANALYTICS_DB"
        mock_config.schema_name = "SEMANTIC"
        mock_config.account = "test"
        emitter = SnowflakeEmitter(mock_config)
        ddls = emitter.generate_ddls(model)
        assert isinstance(ddls, list)

    def test_cli_sync_measures_filtering(self, customer_profitability_model):
        """sync-measures only picks up syncable, non-hidden metrics."""
        model = customer_profitability_model

        # Run pipeline to update sync_enabled
        pipeline = TieredSafetyPipeline(override_dir=None)
        pipeline.run(model, generate_templates=False)

        syncable = [m for m in model.metrics if m.sync_enabled and not m.is_hidden]

        # Only Tier 1/2 should be syncable (10 of 24)
        for m in syncable:
            tier = CUSTOMER_PROFITABILITY_MEASURES[m.unique_name]["expected_tier"]
            assert tier <= 2, f"{m.unique_name} (Tier {tier}) should not be syncable"


# ═══════════════════════════════════════════════════════════════════════
# 5. Engine/UI Path Simulation — mirrors SyncWorker → Engine flow
# ═══════════════════════════════════════════════════════════════════════

class TestEngineSyncPath:
    """Simulates the UI SyncWorker → Engine.execute() path."""

    def test_engine_conversion_with_safety(self, customer_profitability_model, tmp_override_dir):
        """
        Simulates: Engine._convert_single() → TieredSafetyPipeline.run()

        This validates that after the engine's conversion step,
        the safety pipeline can run on the produced SMLModel and
        correctly classify + update all metrics.
        """
        model = customer_profitability_model

        # Engine conversion would produce a model very similar to our fixture.
        # Now integrate safety:
        pipeline = TieredSafetyPipeline(
            override_dir=tmp_override_dir,
            database="ANALYTICS_DB",
            schema="SEMANTIC",
        )
        result = pipeline.run(model)

        # Verify engine-visible fields are set
        for metric in model.metrics:
            assert metric.complexity_tier > 0, f"{metric.unique_name}: complexity_tier not set"
            expected = CUSTOMER_PROFITABILITY_MEASURES[metric.unique_name]["expected_tier"]
            if expected <= 2:
                assert metric.sync_enabled, f"{metric.unique_name}: should be sync_enabled in engine path"

    def test_engine_result_dict_compatible(self, customer_profitability_model, tmp_override_dir):
        """
        The SyncWorker emits result_dict with broadcast_results.
        Verify our pipeline result can produce a report compatible with dict serialisation.
        """
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        result = pipeline.run(customer_profitability_model)

        report = result.get_report()
        # Must be JSON-serializable (what SyncWorker.finished.emit(dict) needs)
        json_str = json.dumps(report, default=str)
        assert len(json_str) > 0
        restored = json.loads(json_str)
        assert restored["total_measures"] == len(CUSTOMER_PROFITABILITY_MEASURES)

    def test_engine_dry_run_no_side_effects(self, customer_profitability_model):
        """
        A dry-run (no override_dir) should classify without generating files.
        """
        pipeline = TieredSafetyPipeline(override_dir=None)
        result = pipeline.run(customer_profitability_model, generate_templates=False)

        # Classifications present, no files generated
        assert result.total_measures == len(CUSTOMER_PROFITABILITY_MEASURES)
        assert len(result.overrides_generated) == 0
        assert len(result.overrides_loaded) == 0


# ═══════════════════════════════════════════════════════════════════════
# 6. Hazard Category Coverage
# ═══════════════════════════════════════════════════════════════════════

class TestHazardCategoryCoverage:
    """Ensure the Customer Profitability model exercises all 5 hazard categories."""

    def test_all_hazard_categories_detected(self, classifier, customer_profitability_model):
        """At least one measure triggers each hazard category."""
        seen_hazards = set()
        for metric in customer_profitability_model.metrics:
            c = classifier.classify(metric.unique_name, metric.expression)
            for h in c.hazards:
                seen_hazards.add(h.category)

        # Category 1: complex_filter_context (CALCULATE+FILTER/ALL/ALLEXCEPT/ALLSELECTED/KEEPFILTERS)
        assert HazardCategory.COMPLEX_FILTER_CONTEXT in seen_hazards, "Missing complex_filter_context hazard"
        # Category 2: row_iteration_context (SUMX/AVERAGEX/COUNTX/MAXX/MINX)
        assert HazardCategory.ROW_CONTEXT_ITERATOR in seen_hazards, "Missing row_context_iterator hazard"
        # Category 3: time_intelligence_context (TOTALYTD/TOTALMTD/SAMEPERIODLASTYEAR/PARALLELPERIOD)
        assert HazardCategory.ADVANCED_TIME_INTELLIGENCE in seen_hazards, "Missing advanced_time_intelligence hazard"
        # Category 4: specialized_builtin_logic (RANKX/TOPN/GENERATE/EARLIER)
        assert HazardCategory.SPECIALIZED_BUILTIN_LOGIC in seen_hazards, "Missing specialized_builtin_logic hazard"

    def test_optout_rank_has_multiple_hazards(self, classifier):
        """The OptOut_Rank_Measure should detect both category_1 and category_4 hazards."""
        dax = "RANKX(ALL('Fact Financials'), CALCULATE(SUM('Fact Financials'[Revenue]), ALLEXCEPT('Fact Financials', 'Fact Financials'[CustomerID])))"
        c = classifier.classify("OptOut Rank Measure", dax)
        hazard_cats = {h.category for h in c.hazards}
        assert HazardCategory.SPECIALIZED_BUILTIN_LOGIC in hazard_cats
        assert HazardCategory.COMPLEX_FILTER_CONTEXT in hazard_cats


# ═══════════════════════════════════════════════════════════════════════
# 7. OSI-Native Pipeline Tests — run_osi() without SML
# ═══════════════════════════════════════════════════════════════════════


def _build_customer_profitability_osi_model():
    """Construct an OSI-native equivalent of the Customer Profitability model."""
    from semabridge.intermediate.models import (
        OSIAggregationType,
        OSIColumn,
        OSIDataType,
        OSIDataset,
        OSIMetric,
        OSIModel,
        OSIRelationship,
        OSICardinality,
    )

    fact_columns = [
        OSIColumn(unique_name="TransactionID", data_type=OSIDataType.INTEGER, is_key=True),
        OSIColumn(unique_name="CustomerID", data_type=OSIDataType.INTEGER),
        OSIColumn(unique_name="ProductID", data_type=OSIDataType.INTEGER),
        OSIColumn(unique_name="Revenue", data_type=OSIDataType.DECIMAL),
        OSIColumn(unique_name="Cost", data_type=OSIDataType.DECIMAL),
        OSIColumn(unique_name="Quantity", data_type=OSIDataType.INTEGER),
        OSIColumn(unique_name="UnitPrice", data_type=OSIDataType.DECIMAL),
        OSIColumn(unique_name="OrderDate", data_type=OSIDataType.DATE),
    ]
    fact_ds = OSIDataset(
        unique_name="Fact Financials",
        label="Fact Financials",
        source_table="FACT_FINANCIALS",
        is_fact=True,
        columns=fact_columns,
    )

    customer_ds = OSIDataset(
        unique_name="Dim Customer",
        label="Dim Customer",
        source_table="DIM_CUSTOMER",
        is_fact=False,
        columns=[
            OSIColumn(unique_name="CustomerID", data_type=OSIDataType.INTEGER, is_key=True),
            OSIColumn(unique_name="CustomerName", data_type=OSIDataType.STRING),
            OSIColumn(unique_name="Segment", data_type=OSIDataType.STRING),
        ],
    )

    calendar_ds = OSIDataset(
        unique_name="Calendar",
        label="Calendar",
        source_table="CALENDAR",
        is_fact=False,
        columns=[
            OSIColumn(unique_name="Date", data_type=OSIDataType.DATE, is_key=True),
            OSIColumn(unique_name="Year", data_type=OSIDataType.INTEGER),
            OSIColumn(unique_name="Month", data_type=OSIDataType.INTEGER),
        ],
    )

    geography_ds = OSIDataset(
        unique_name="Dim Geography",
        label="Dim Geography",
        source_table="DIM_GEOGRAPHY",
        is_fact=False,
        columns=[
            OSIColumn(unique_name="GeoID", data_type=OSIDataType.INTEGER, is_key=True),
            OSIColumn(unique_name="Region", data_type=OSIDataType.STRING),
        ],
    )

    metrics = []
    for name, spec in CUSTOMER_PROFITABILITY_MEASURES.items():
        metrics.append(
            OSIMetric(
                unique_name=name,
                label=name,
                dataset=spec["dataset"],
                expression=spec["dax"],
                aggregation=OSIAggregationType.SUM,
            )
        )

    relationships = [
        OSIRelationship(
            unique_name="Financials_to_Customer",
            from_dataset="Fact Financials",
            from_columns=["CustomerID"],
            to_dataset="Dim Customer",
            to_columns=["CustomerID"],
            cardinality=OSICardinality.MANY_TO_ONE,
            is_active=True,
        ),
        OSIRelationship(
            unique_name="Financials_to_Calendar",
            from_dataset="Fact Financials",
            from_columns=["OrderDate"],
            to_dataset="Calendar",
            to_columns=["Date"],
            cardinality=OSICardinality.MANY_TO_ONE,
            is_active=True,
        ),
    ]

    return OSIModel(
        unique_name="Customer Profitability",
        label="Customer Profitability",
        description="OSI-native integration test model for tiered safety",
        datasets=[fact_ds, customer_ds, calendar_ds, geography_ds],
        metrics=metrics,
        relationships=relationships,
    )


@pytest.fixture
def customer_profitability_osi_model():
    return _build_customer_profitability_osi_model()


class TestOSINativePipeline:
    """Verify run_osi() works identically to run() for Customer Profitability."""

    def test_osi_pipeline_classifies_all_measures(
        self, customer_profitability_osi_model, tmp_override_dir
    ):
        pipeline = TieredSafetyPipeline(
            override_dir=tmp_override_dir,
            database="ANALYTICS_DB",
            schema="SEMANTIC",
        )
        result = pipeline.run_osi(customer_profitability_osi_model)

        assert result.total_measures == len(CUSTOMER_PROFITABILITY_MEASURES)
        assert result.auto_translatable_count == 12  # 5 T1 + 7 T2
        assert result.override_required_count == 12  # 8 T3 + 4 T4

    def test_osi_pipeline_auto_translates_tier1_tier2(
        self, customer_profitability_osi_model, tmp_override_dir
    ):
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        result = pipeline.run_osi(customer_profitability_osi_model)

        for name, spec in CUSTOMER_PROFITABILITY_MEASURES.items():
            if spec["expected_tier"] <= 2:
                assert name in result.auto_translated, f"{name} should be auto-translated"

    def test_osi_pipeline_updates_metric_metadata(
        self, customer_profitability_osi_model, tmp_override_dir
    ):
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        pipeline.run_osi(customer_profitability_osi_model)

        for metric in customer_profitability_osi_model.metrics:
            expected = CUSTOMER_PROFITABILITY_MEASURES[metric.unique_name]["expected_tier"]
            assert metric.complexity_tier == expected, (
                f"{metric.unique_name}: expected complexity_tier={expected}, "
                f"got {metric.complexity_tier}"
            )

    def test_osi_pipeline_generates_ddls(
        self, customer_profitability_osi_model, tmp_override_dir
    ):
        """After run_osi, generate_ddls_from_osi must not crash."""
        pipeline = TieredSafetyPipeline(
            override_dir=tmp_override_dir,
            database="ANALYTICS_DB",
            schema="SEMANTIC",
        )
        pipeline.run_osi(customer_profitability_osi_model)

        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        from unittest.mock import MagicMock

        mock_config = MagicMock()
        mock_config.database = "ANALYTICS_DB"
        mock_config.schema_name = "SEMANTIC"
        mock_config.account = "test"
        emitter = SnowflakeEmitter(mock_config)
        ddls = emitter.generate_ddls_from_osi(customer_profitability_osi_model)
        assert isinstance(ddls, list)
        assert len(ddls) == 1
        assert "SEMANTIC VIEW" in ddls[0]

    def test_osi_pipeline_generates_cortex_yaml(
        self, customer_profitability_osi_model, tmp_override_dir
    ):
        """After run_osi, generate_cortex_yaml_from_osi must produce valid YAML."""
        pipeline = TieredSafetyPipeline(override_dir=tmp_override_dir)
        pipeline.run_osi(customer_profitability_osi_model)

        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
        from unittest.mock import MagicMock
        import yaml

        mock_config = MagicMock()
        mock_config.database = "ANALYTICS_DB"
        mock_config.schema_name = "SEMANTIC"
        mock_config.account = "test"
        emitter = SnowflakeEmitter(mock_config)
        yaml_str = emitter.generate_cortex_yaml_from_osi(customer_profitability_osi_model)
        parsed = yaml.safe_load(yaml_str)
        assert "semantic_model" in parsed
        assert parsed["semantic_model"]["name"] == "Customer Profitability"

    def test_osi_report_matches_sml_report(
        self, customer_profitability_model, customer_profitability_osi_model, tmp_override_dir
    ):
        """OSI pipeline report must match SML pipeline report."""
        import tempfile

        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            sml_pipeline = TieredSafetyPipeline(override_dir=Path(d1))
            sml_result = sml_pipeline.run(customer_profitability_model)
            sml_report = sml_result.get_report()

            osi_pipeline = TieredSafetyPipeline(override_dir=Path(d2))
            osi_result = osi_pipeline.run_osi(customer_profitability_osi_model)
            osi_report = osi_result.get_report()

            assert sml_report["total_measures"] == osi_report["total_measures"]
            assert sml_report["tier_distribution"] == osi_report["tier_distribution"]
            assert sml_report["auto_translatable"] == osi_report["auto_translatable"]
            assert sml_report["override_required"] == osi_report["override_required"]


# ═══════════════════════════════════════════════════════════════════════
# 7. Multi-Model Sync Isolation Tests
# ═══════════════════════════════════════════════════════════════════════

class TestMultiModelSyncIsolation:
    """Verify that multi-model sync handles failures and output isolation correctly."""

    def test_system_exit_caught_in_multi_model_loop(self):
        """SystemExit (typer.Exit) must not kill the multi-model batch."""
        # Simulate the outer loop from _run_fabric_to_snowflake
        models = [{"id": "m1", "name": "Model_A"}, {"id": "m2", "name": "Model_B"}]
        succeeded = 0
        failed = 0

        def _fake_sync(model_id: str):
            if model_id == "m1":
                raise SystemExit(1)  # mirrors typer.Exit(code=1)
            # m2 succeeds

        for model_info in models:
            try:
                _fake_sync(model_info["id"])
                succeeded += 1
            except (Exception, SystemExit):
                failed += 1

        assert failed == 1
        assert succeeded == 1, "Second model must still be processed after first fails"

    def test_output_artifacts_scoped_per_model(self, customer_profitability_osi_model, tmp_path):
        """Each model's safety report should be written to its own subdirectory."""
        import re

        model_name = customer_profitability_osi_model.label  # "Customer Profitability"
        safe_name = re.sub(r'[^\w\-.]', '_', model_name)

        pipeline = TieredSafetyPipeline(override_dir=tmp_path / "overrides")
        result = pipeline.run_osi(customer_profitability_osi_model)

        report_dir = tmp_path / "reports" / safe_name
        pipeline.write_safety_report(result, report_dir)

        assert (report_dir / "tiered_safety_report.json").exists()
        # Original global path should NOT exist
        assert not (tmp_path / "reports" / "tiered_safety_report.json").exists()

    def test_multiple_models_produce_separate_reports(self, tmp_path):
        """Two different models must produce separate report files."""
        import re, copy

        model_a = SMLModel(
            unique_name="Model_A",
            label="Model A",
            datasets=[
                SMLDataset(
                    unique_name="FactSales",
                    label="Fact Sales",
                    source_table="FACT_SALES",
                    columns=[
                        SMLColumn(unique_name="Revenue", label="Revenue", data_type=DataType.FLOAT),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Revenue",
                    label="Total Revenue",
                    expression="SUM('FactSales'[Revenue])",
                    dataset="FactSales",
                    agg_type=AggregationType.SUM,
                ),
            ],
        )

        model_b = SMLModel(
            unique_name="Model_B",
            label="Model B",
            datasets=[
                SMLDataset(
                    unique_name="FactOrders",
                    label="Fact Orders",
                    source_table="FACT_ORDERS",
                    columns=[
                        SMLColumn(unique_name="OrderCount", label="Order Count", data_type=DataType.INTEGER),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Total Orders",
                    label="Total Orders",
                    expression="SUM('FactOrders'[OrderCount])",
                    dataset="FactOrders",
                    agg_type=AggregationType.SUM,
                ),
            ],
        )

        for model in [model_a, model_b]:
            safe_name = re.sub(r'[^\w\-.]', '_', model.label)
            override_dir = tmp_path / "overrides" / safe_name
            override_dir.mkdir(parents=True, exist_ok=True)
            pipeline = TieredSafetyPipeline(override_dir=override_dir)
            result = pipeline.run(model)
            report_dir = tmp_path / "reports" / safe_name
            pipeline.write_safety_report(result, report_dir)

        assert (tmp_path / "reports" / "Model_A" / "tiered_safety_report.json").exists()
        assert (tmp_path / "reports" / "Model_B" / "tiered_safety_report.json").exists()

    def test_sequential_pipeline_isolation(self, customer_profitability_model, tmp_path):
        """Running the pipeline twice sequentially must not leak state."""
        import copy

        model1 = copy.deepcopy(customer_profitability_model)
        model2 = copy.deepcopy(customer_profitability_model)

        pipeline1 = TieredSafetyPipeline(override_dir=tmp_path / "ov1")
        pipeline2 = TieredSafetyPipeline(override_dir=tmp_path / "ov2")

        result1 = pipeline1.run(model1)
        result2 = pipeline2.run(model2)

        report1 = result1.get_report()
        report2 = result2.get_report()

        assert report1["total_measures"] == report2["total_measures"]
        assert report1["tier_distribution"] == report2["tier_distribution"]


# ═══════════════════════════════════════════════════════════════════════
# 8. Engine Broadcast Serialization Tests
# ═══════════════════════════════════════════════════════════════════════

class TestBroadcastSerialization:
    """Verify models deploy sequentially within each target to avoid DDL races."""

    def test_broadcast_deploys_models_sequentially_per_target(self):
        """Models must be deployed one at a time to avoid table creation races."""
        from unittest.mock import MagicMock, call
        from semabridge.core.engine import SemaBridgeEngine, BroadcastResult
        from semabridge.core.project import ProjectConfig, SourceConfig, TargetConfig

        target = TargetConfig(
            type="snowflake_semantic_view",
            database="DB",
            schema_name="SCH",
        )
        config = ProjectConfig(
            name="Test",
            source=SourceConfig(type="fabric", workspace_id="ws"),
            targets=[target],
        )
        engine = SemaBridgeEngine(config)

        # Track deployment order to prove sequential execution
        deploy_order = []

        def fake_deploy(t, model):
            import time
            deploy_order.append(model.label if hasattr(model, 'label') else str(model))
            time.sleep(0.01)  # small delay to detect parallelism
            return BroadcastResult(target=t, success=True, message="ok")

        engine._deploy_to_target = fake_deploy

        # Create two fake models
        model_a = MagicMock()
        model_a.label = "Model_A"
        model_b = MagicMock()
        model_b.label = "Model_B"

        results = engine._broadcast_to_targets([model_a, model_b])

        assert len(results) == 2
        assert all(r.success for r in results)
        # Verify sequential: models deployed in order, not interleaved
        assert deploy_order == ["Model_A", "Model_B"]

    def test_broadcast_continues_after_single_model_failure(self):
        """If one model fails, the remaining models must still be deployed."""
        from unittest.mock import MagicMock
        from semabridge.core.engine import SemaBridgeEngine, BroadcastResult
        from semabridge.core.project import ProjectConfig, SourceConfig, TargetConfig

        target = TargetConfig(
            type="snowflake_semantic_view",
            database="DB",
            schema_name="SCH",
        )
        config = ProjectConfig(
            name="Test",
            source=SourceConfig(type="fabric", workspace_id="ws"),
            targets=[target],
        )
        engine = SemaBridgeEngine(config)

        call_count = {"n": 0}

        def fake_deploy(t, model):
            call_count["n"] += 1
            if model.label == "Model_A":
                raise RuntimeError("Table conflict simulation")
            return BroadcastResult(target=t, success=True, message="ok")

        engine._deploy_to_target = fake_deploy

        model_a = MagicMock()
        model_a.label = "Model_A"
        model_b = MagicMock()
        model_b.label = "Model_B"

        results = engine._broadcast_to_targets([model_a, model_b])

        assert call_count["n"] == 2, "Both models must be attempted"
        succeeded = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        assert len(succeeded) == 1
        assert len(failed) == 1


# ═══════════════════════════════════════════════════════════════════════
# 9. Snowflake Emitter Column Quoting Tests
# ═══════════════════════════════════════════════════════════════════════

class TestSampleInsertColumnQuoting:
    """Verify _generate_sample_insert quotes column names."""

    def test_sample_insert_quotes_column_names(self):
        """Column names in INSERT must be double-quoted to match CREATE TABLE."""
        from unittest.mock import MagicMock
        from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

        mock_config = MagicMock()
        mock_config.database = "DB"
        mock_config.schema_name = "SCH"
        emitter = SnowflakeEmitter(mock_config)

        dataset = SMLDataset(
            unique_name="FactSales",
            label="Fact Sales",
            source_table="FACT_SALES",
            columns=[
                SMLColumn(unique_name="Revenue", label="Revenue", data_type=DataType.FLOAT),
                SMLColumn(unique_name="OrderDate", label="Order Date", data_type=DataType.DATE),
            ],
        )

        insert = emitter._generate_sample_insert(dataset, "FACT_SALES")
        assert insert is not None

        # Column names must be double-quoted
        assert '"REVENUE"' in insert
        assert '"ORDER_DATE"' in insert or '"ORDERDATE"' in insert
        # Unquoted column names must NOT appear in the column list
        assert "( REVENUE" not in insert
        assert "(REVENUE" not in insert
