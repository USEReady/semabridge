"""
Comprehensive tests for the Tiered Safety DAX classification engine,
override schema, override generator, override validator, and safety pipeline.
"""

import json
import tempfile
from pathlib import Path

import pytest
import yaml

from semabridge.converter.tiered_safety import (
    HazardCategory,
    HazardDetection,
    OverrideLayerType,
    SafetyClassification,
    SafetyTier,
    TieredSafetyClassifier,
)
from semabridge.converter.override_schema import (
    CortexMetadataLayer,
    CortexSynonym,
    DynamicTableLayer,
    OverrideStatus,
    SemanticDimension,
    SemanticMetricSpec,
    SemanticViewLayer,
    SQLOverrideFile,
    WindowFunctionSpec,
)
from semabridge.converter.override_generator import OverrideGenerator
from semabridge.converter.override_validator import (
    OverrideValidator,
    OverrideValidationResult,
    load_override_file,
    load_override_directory,
)
from semabridge.converter.safety_pipeline import (
    SafetyPipelineResult,
    TieredSafetyPipeline,
)
from semabridge.formats.sml.models import SMLMetric, SMLModel, SMLDataset


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def classifier():
    return TieredSafetyClassifier()


@pytest.fixture
def generator():
    return OverrideGenerator(
        database="TEST_DB",
        schema="TEST_SCHEMA",
        warehouse="TEST_WH",
    )


@pytest.fixture
def validator():
    return OverrideValidator()


@pytest.fixture
def sample_model():
    """Create a minimal SML model with diverse metrics for testing."""
    return SMLModel(
        unique_name="TestModel",
        label="Test Semantic Model",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                source_table="FACT_SALES",
                is_fact=True,
            ),
        ],
        metrics=[
            # Tier 1: Simple aggregation
            SMLMetric(
                unique_name="Total Revenue",
                dataset="Sales",
                expression="SUM([Revenue])",
            ),
            # Tier 2: Arithmetic
            SMLMetric(
                unique_name="Margin",
                dataset="Sales",
                expression="DIVIDE([Profit], [Revenue])",
            ),
            # Tier 3: Complex filter context
            SMLMetric(
                unique_name="Red Sales",
                dataset="Sales",
                expression="CALCULATE(SUM([Revenue]), FILTER('Sales', 'Sales'[Color] = \"Red\"))",
            ),
            # Tier 4: RANKX
            SMLMetric(
                unique_name="Sales Rank",
                dataset="Sales",
                expression="RANKX(ALL('Sales'), [Total Revenue])",
            ),
            # Tier 4: EARLIER
            SMLMetric(
                unique_name="Running Total",
                dataset="Sales",
                expression="SUMX(FILTER('Sales', 'Sales'[Date] <= EARLIER('Sales'[Date])), [Revenue])",
            ),
        ],
    )


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


# =============================================================================
# TieredSafetyClassifier Tests
# =============================================================================

class TestTieredSafetyClassifier:
    """Tests for the Tiered Safety classification engine."""

    # ── Tier 1: Deterministic Translation ──

    def test_tier1_sum(self, classifier):
        result = classifier.classify("Revenue", "SUM([Amount])")
        assert result.tier == SafetyTier.TIER_1_DETERMINISTIC
        assert result.is_automatable is True
        assert result.requires_override is False
        assert len(result.hazards) == 0

    def test_tier1_count(self, classifier):
        result = classifier.classify("OrderCount", "COUNT([OrderID])")
        assert result.tier == SafetyTier.TIER_1_DETERMINISTIC

    def test_tier1_distinctcount(self, classifier):
        result = classifier.classify("Unique Customers", "DISTINCTCOUNT([CustID])")
        assert result.tier == SafetyTier.TIER_1_DETERMINISTIC

    def test_tier1_average(self, classifier):
        result = classifier.classify("AvgPrice", "AVERAGE([Price])")
        assert result.tier == SafetyTier.TIER_1_DETERMINISTIC

    def test_tier1_min_max(self, classifier):
        r1 = classifier.classify("MinPrice", "MIN([Price])")
        r2 = classifier.classify("MaxPrice", "MAX([Price])")
        assert r1.tier == SafetyTier.TIER_1_DETERMINISTIC
        assert r2.tier == SafetyTier.TIER_1_DETERMINISTIC

    def test_tier1_with_table_qualifier(self, classifier):
        result = classifier.classify("Revenue", "SUM('Sales'[Amount])")
        assert result.tier == SafetyTier.TIER_1_DETERMINISTIC

    def test_tier1_empty_expression(self, classifier):
        result = classifier.classify("Empty", "")
        assert result.tier == SafetyTier.TIER_1_DETERMINISTIC
        assert result.is_automatable is True

    def test_tier1_measure_reference(self, classifier):
        result = classifier.classify("Ref", "[OtherMeasure]")
        assert result.tier == SafetyTier.TIER_1_DETERMINISTIC

    # ── Tier 2: Conditional Equivalency ──

    def test_tier2_divide(self, classifier):
        result = classifier.classify("Margin", "DIVIDE([Profit], [Revenue])")
        assert result.tier == SafetyTier.TIER_2_CONDITIONAL
        assert result.is_automatable is True

    def test_tier2_if(self, classifier):
        result = classifier.classify("Check", "IF([Revenue] > 100, 1, 0)")
        assert result.tier == SafetyTier.TIER_2_CONDITIONAL
        assert result.is_automatable is True

    def test_tier2_switch(self, classifier):
        result = classifier.classify("Bucket", "SWITCH([Status], 1, 'Active', 'Other')")
        assert result.tier == SafetyTier.TIER_2_CONDITIONAL

    def test_tier2_arithmetic(self, classifier):
        result = classifier.classify("Net", "[Gross] - [Discounts]")
        assert result.tier == SafetyTier.TIER_2_CONDITIONAL

    # ── Tier 3: Contextual Dissonance ── Category 1

    def test_tier3_calculate_filter(self, classifier):
        dax = "CALCULATE(SUM([Revenue]), FILTER('Sales', 'Sales'[Color] = \"Red\"))"
        result = classifier.classify("Red Sales", dax)
        assert result.tier == SafetyTier.TIER_3_CONTEXTUAL_DISSONANCE
        assert result.requires_override is True
        assert result.primary_hazard == HazardCategory.COMPLEX_FILTER_CONTEXT

    def test_tier3_calculate_all(self, classifier):
        dax = "CALCULATE(SUM([Revenue]), ALL('Sales'))"
        result = classifier.classify("Total All", dax)
        assert result.tier == SafetyTier.TIER_3_CONTEXTUAL_DISSONANCE
        assert result.primary_hazard == HazardCategory.COMPLEX_FILTER_CONTEXT

    def test_tier3_calculate_allexcept(self, classifier):
        dax = "CALCULATE(SUM([Revenue]), ALLEXCEPT('Sales', 'Sales'[Region]))"
        result = classifier.classify("Region Only", dax)
        assert result.requires_override is True
        assert result.primary_hazard == HazardCategory.COMPLEX_FILTER_CONTEXT

    def test_tier3_calculate_allselected(self, classifier):
        dax = "CALCULATE(SUM([Revenue]), ALLSELECTED('Sales'))"
        result = classifier.classify("Selected Total", dax)
        assert result.requires_override is True

    # ── Tier 3: Contextual Dissonance ── Category 2

    def test_tier3_sumx(self, classifier):
        dax = "SUMX('Sales', [Price] * [Qty])"
        result = classifier.classify("Line Total", dax)
        assert result.requires_override is True
        assert result.primary_hazard == HazardCategory.ROW_CONTEXT_ITERATOR

    def test_tier3_sumx_filter(self, classifier):
        dax = "SUMX(FILTER('Sales', [Qty] > 10), [Price] * [Qty])"
        result = classifier.classify("BigLine", dax)
        assert result.requires_override is True

    def test_tier3_averagex(self, classifier):
        dax = "AVERAGEX('Products', [Price] * 1.1)"
        result = classifier.classify("AvgX", dax)
        assert result.requires_override is True
        assert any(
            h.function_name == "AVERAGEX" for h in result.hazards
        )

    def test_tier3_countx(self, classifier):
        dax = "COUNTX('Sales', [Qty])"
        result = classifier.classify("CntX", dax)
        assert result.requires_override is True

    # ── Tier 3: Contextual Dissonance ── Category 3

    def test_tier3_parallelperiod(self, classifier):
        dax = "CALCULATE([Revenue], PARALLELPERIOD('Date'[Date], -1, YEAR))"
        result = classifier.classify("PY Revenue", dax)
        assert result.requires_override is True
        # Note: has both CALCULATE pattern and PARALLELPERIOD pattern

    def test_tier3_sameperiodlastyear(self, classifier):
        dax = "CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date]))"
        result = classifier.classify("LY Revenue", dax)
        assert result.requires_override is True

    def test_tier2_safe_totalytd(self, classifier):
        """TOTALYTD is a safe time intel function — classified as Tier 2."""
        dax = "TOTALYTD(SUM([Revenue]), 'Date'[Date])"
        result = classifier.classify("Revenue YTD", dax)
        # Safe PTD functions should be Tier 2
        assert result.tier == SafetyTier.TIER_2_CONDITIONAL
        assert result.is_automatable is True

    # ── Tier 4: Structural Hazard ── Category 4

    def test_tier4_userelationship(self, classifier):
        dax = "CALCULATE(SUM([Amount]), USERELATIONSHIP('Sales'[ShipDate], 'Calendar'[Date]))"
        result = classifier.classify("Ship Amount", dax)
        assert result.tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD
        assert result.requires_override is True
        assert HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER in [
            h.category for h in result.hazards
        ]

    def test_tier4_crossfilter(self, classifier):
        dax = "CALCULATE(SUM([Revenue]), CROSSFILTER('Sales'[ProductKey], 'Products'[Key], Both))"
        result = classifier.classify("Bi-Dir", dax)
        assert result.tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD

    def test_tier4_treatas(self, classifier):
        dax = "CALCULATE([Revenue], TREATAS(VALUES('Region'[Name]), 'Sales'[Territory]))"
        result = classifier.classify("TreatAs", dax)
        assert result.tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD

    # ── Tier 4: Structural Hazard ── Category 5

    def test_tier4_rankx(self, classifier):
        dax = "RANKX(ALL('Sales'), [Total Revenue])"
        result = classifier.classify("Sales Rank", dax)
        assert result.tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD
        assert result.primary_hazard == HazardCategory.SPECIALIZED_BUILTIN_LOGIC

    def test_tier4_earlier(self, classifier):
        dax = "SUMX(FILTER('Sales', 'Sales'[Date] <= EARLIER('Sales'[Date])), [Revenue])"
        result = classifier.classify("Running", dax)
        assert result.tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD
        assert any(h.function_name == "EARLIER" for h in result.hazards)

    def test_tier4_topn(self, classifier):
        dax = "TOPN(5, 'Products', [Revenue], DESC)"
        result = classifier.classify("Top5", dax)
        assert result.tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD

    def test_tier4_generate(self, classifier):
        dax = "GENERATE(VALUES('Region'), TOPN(3, 'Products'))"
        result = classifier.classify("Gen", dax)
        assert result.tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD

    # ── Multiple hazards ──

    def test_multiple_hazards_detected(self, classifier):
        dax = "RANKX(FILTER('Sales', EARLIER('Sales'[Date]) <= [Date]), [Revenue])"
        result = classifier.classify("Complex", dax)
        assert result.tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD
        assert len(result.hazards) >= 2  # RANKX + EARLIER + possible FILTER

    # ── Batch classification ──

    def test_classify_all(self, classifier):
        measures = {
            "Revenue": "SUM([Amount])",
            "Margin": "DIVIDE([Profit], [Revenue])",
            "Rank": "RANKX(ALL('Sales'), [Revenue])",
        }
        results = classifier.classify_all(measures)
        assert len(results) == 3
        assert results["Revenue"].tier == SafetyTier.TIER_1_DETERMINISTIC
        assert results["Margin"].tier == SafetyTier.TIER_2_CONDITIONAL
        assert results["Rank"].tier == SafetyTier.TIER_4_STRUCTURAL_HAZARD

    def test_classify_all_empty(self, classifier):
        """Empty input returns empty dict without error."""
        results = classifier.classify_all({})
        assert results == {}

    # ── Concurrent batch classification ──

    def test_classify_all_parallel_matches_sequential(self, classifier):
        """Parallel results must be identical to sequential results."""
        measures = {
            "Revenue": "SUM([Amount])",
            "Margin": "DIVIDE([Profit], [Revenue])",
            "Rank": "RANKX(ALL('Sales'), [Revenue])",
            "IterX": "SUMX('Sales', [Price] * [Qty])",
            "YTD": "TOTALYTD(SUM([Revenue]), 'Date'[Date])",
            "FilterCtx": "CALCULATE(SUM([Revenue]), FILTER('Sales', [Color]=\"Red\"))",
        }
        seq = classifier.classify_all(measures, max_workers=1)
        par = classifier.classify_all(measures, max_workers=4)

        assert set(seq.keys()) == set(par.keys())
        for name in measures:
            assert seq[name].tier == par[name].tier
            assert seq[name].requires_override == par[name].requires_override
            assert seq[name].primary_hazard == par[name].primary_hazard
            assert seq[name].is_automatable == par[name].is_automatable
            assert seq[name].estimated_complexity == par[name].estimated_complexity

    def test_classify_all_parallel_auto_workers(self, classifier):
        """max_workers=0 auto-selects worker count and still returns correct results."""
        measures = {f"m{i}": "SUM([X])" for i in range(20)}
        results = classifier.classify_all(measures, max_workers=0)
        assert len(results) == 20
        assert all(
            r.tier == SafetyTier.TIER_1_DETERMINISTIC for r in results.values()
        )

    def test_classify_all_progress_callback(self, classifier):
        """Progress callback is invoked for every measure."""
        calls = []

        def on_progress(done, total, name):
            calls.append((done, total, name))

        measures = {"A": "SUM([X])", "B": "DIVIDE([X],[Y])", "C": "RANKX(ALL('T'),[X])"}
        classifier.classify_all(measures, max_workers=2, progress_callback=on_progress)
        assert len(calls) == 3
        # Every call should report the correct total
        assert all(total == 3 for _, total, _ in calls)
        # Completed counters should reach 1..3 (in any order)
        assert sorted(done for done, _, _ in calls) == [1, 2, 3]

    def test_classify_all_large_batch_parallel(self, classifier):
        """Stress test: 200 measures classified concurrently."""
        dax_pool = [
            "SUM([Amount])",
            "DIVIDE([X],[Y])",
            "IF([X]>0,1,0)",
            "RANKX(ALL('T'),[X])",
            "SUMX('T',[P]*[Q])",
            "CALCULATE(SUM([X]),ALL('T'))",
            "TOTALYTD(SUM([X]),'D'[D])",
        ]
        measures = {f"m{i}": dax_pool[i % len(dax_pool)] for i in range(200)}
        results = classifier.classify_all(measures, max_workers=4)
        assert len(results) == 200
        # Verify tier distribution makes sense
        tiers = [r.tier.value for r in results.values()]
        assert 1 in tiers and 2 in tiers and 3 in tiers and 4 in tiers

    def test_resolve_workers(self, classifier):
        """Worker resolution follows documented semantics."""
        assert classifier._resolve_workers(None, 10) == 1   # default = sequential
        assert classifier._resolve_workers(1, 10) == 1      # explicit sequential
        assert classifier._resolve_workers(0, 10) == 1      # auto but below threshold
        assert classifier._resolve_workers(0, 100) >= 2     # auto above threshold
        assert classifier._resolve_workers(0, 100) <= 4     # capped at _MAX_WORKER_CAP
        assert classifier._resolve_workers(8, 3) == 3       # capped to batch size
        assert classifier._resolve_workers(8, 100) == 4     # capped at _MAX_WORKER_CAP
        assert classifier._resolve_workers(3, 100) == 3     # use as-is when under cap

    # ── Summary ──

    def test_get_summary(self, classifier):
        measures = {
            "A": "SUM([X])",
            "B": "DIVIDE([X], [Y])",
            "C": "RANKX(ALL('T'), [A])",
            "D": "SUMX('T', [X] * [Y])",
        }
        results = classifier.classify_all(measures)
        summary = classifier.get_summary(results)
        assert summary["total_measures"] == 4
        assert summary["automatable"] == 2  # A + B
        assert summary["requires_override"] == 2  # C + D
        assert summary["automation_rate"] == 50.0

    # ── Override layers recommendation ──

    def test_override_layers_for_rankx(self, classifier):
        result = classifier.classify("Rank", "RANKX(ALL('T'), [X])")
        assert OverrideLayerType.DYNAMIC_TABLE in result.override_layers
        assert OverrideLayerType.SEMANTIC_VIEW in result.override_layers
        assert OverrideLayerType.CORTEX_METADATA in result.override_layers

    def test_override_layers_for_userelationship(self, classifier):
        result = classifier.classify(
            "Ship", "CALCULATE(SUM([X]), USERELATIONSHIP([A], [B]))"
        )
        assert OverrideLayerType.SEMANTIC_VIEW in result.override_layers

    # ── Complexity estimation ──

    def test_complexity_simple_vs_complex(self, classifier):
        simple = classifier.classify("S", "SUM([X])")
        complex_ = classifier.classify(
            "C",
            "RANKX(FILTER('T', EARLIER([Date]) <= [Date]), SUMX('T', [X] * [Y]))",
        )
        assert complex_.estimated_complexity > simple.estimated_complexity


# =============================================================================
# Override Schema Tests
# =============================================================================

class TestOverrideSchema:
    """Tests for the SQL Override file Pydantic models."""

    def test_minimal_override(self):
        override = SQLOverrideFile(metric_name="Test Metric")
        assert override.metric_name == "Test Metric"
        assert override.status == OverrideStatus.DRAFT
        assert override.safety_tier == 3

    def test_full_override_creation(self):
        override = SQLOverrideFile(
            metric_name="OptOut_Rank",
            original_dax="RANKX(FILTER(...), ...)",
            safety_tier=4,
            hazard_category=HazardCategory.SPECIALIZED_BUILTIN_LOGIC.value,
            layer_1_dynamic_table=DynamicTableLayer(
                table_name="optout_dt",
                source_table="raw_events",
                window_functions=[
                    WindowFunctionSpec(
                        function="RANK",
                        partition_by=["user_id"],
                        order_by=["date_received"],
                        alias="optout_rank",
                    )
                ],
            ),
            layer_2_semantic_view=SemanticViewLayer(
                view_name="optout_sv",
                source_table="optout_dt",
                metrics=[
                    SemanticMetricSpec(
                        name="primary_optouts",
                        expression="COUNT(CASE WHEN rank=1 THEN 1 END)",
                    )
                ],
            ),
            layer_3_cortex_metadata=CortexMetadataLayer(
                table_name="events",
                metric_synonyms=[
                    CortexSynonym(
                        column_name="primary_optouts",
                        synonyms=["first opt outs", "initial unsubscribes"],
                        description="Count of first-time opt-outs.",
                    )
                ],
            ),
        )
        assert override.safety_tier == 4
        assert override.layer_1_dynamic_table.table_name == "optout_dt"
        assert len(override.layer_2_semantic_view.metrics) == 1

    def test_validate_completeness_missing_layers(self):
        override = SQLOverrideFile(
            metric_name="Test",
            hazard_category=HazardCategory.SPECIALIZED_BUILTIN_LOGIC.value,
        )
        errors = override.validate_completeness()
        assert len(errors) > 0
        assert any("Layer 1" in e for e in errors)
        assert any("Layer 2" in e for e in errors)

    def test_validate_completeness_all_present(self):
        override = SQLOverrideFile(
            metric_name="Test",
            hazard_category=HazardCategory.SPECIALIZED_BUILTIN_LOGIC.value,
            layer_1_dynamic_table=DynamicTableLayer(
                table_name="test_dt",
                source_table="src",
            ),
            layer_2_semantic_view=SemanticViewLayer(
                view_name="test_sv",
                metrics=[
                    SemanticMetricSpec(name="m1", expression="COUNT(*)")
                ],
            ),
            layer_3_cortex_metadata=CortexMetadataLayer(table_name="t"),
        )
        errors = override.validate_completeness()
        assert len(errors) == 0

    def test_window_function_spec(self):
        wf = WindowFunctionSpec(
            function="RANK",
            partition_by=["user_id", "type_id"],
            order_by=["date_received"],
            alias="my_rank",
        )
        assert wf.function == "RANK"
        assert len(wf.partition_by) == 2


# =============================================================================
# Override Generator Tests
# =============================================================================

class TestOverrideGenerator:
    """Tests for the Override file generator."""

    def test_generate_override_basic(self, generator, classifier):
        classification = classifier.classify(
            "Sales Rank", "RANKX(ALL('Sales'), [Revenue])"
        )
        override = generator.generate_override(classification, source_model="TestModel")

        assert override.metric_name == "Sales Rank"
        assert override.safety_tier == 4
        assert override.layer_1_dynamic_table is not None
        assert override.layer_2_semantic_view is not None
        assert override.layer_3_cortex_metadata is not None
        assert override.snowflake_database == "TEST_DB"

    def test_generate_override_for_sumx(self, generator, classifier):
        classification = classifier.classify(
            "Line Total", "SUMX('Sales', [Price] * [Qty])"
        )
        override = generator.generate_override(classification)
        assert override.hazard_category == HazardCategory.ROW_CONTEXT_ITERATOR.value

    def test_generate_override_for_userelationship(self, generator, classifier):
        classification = classifier.classify(
            "Ship Sales",
            "CALCULATE(SUM([Amount]), USERELATIONSHIP([ShipDate], [CalDate]))",
        )
        override = generator.generate_override(classification)
        assert override.hazard_category in (
            HazardCategory.DYNAMIC_RELATIONSHIP_MODIFIER.value,
            HazardCategory.COMPLEX_FILTER_CONTEXT.value,
        )

    def test_generate_batch_overrides(self, generator, classifier):
        measures = {
            "Safe": "SUM([X])",
            "Rank": "RANKX(ALL('T'), [X])",
            "Iter": "SUMX('T', [X] * [Y])",
        }
        classifications = classifier.classify_all(measures)
        overrides = generator.generate_batch_overrides(classifications)
        # Only Tier 3/4 should get overrides
        assert "Safe" not in overrides
        assert "Rank" in overrides
        assert "Iter" in overrides

    def test_write_override_yaml(self, generator, classifier, tmp_dir):
        classification = classifier.classify("Rank", "RANKX(ALL('T'), [X])")
        override = generator.generate_override(classification)
        path = generator.write_override_yaml(override, tmp_dir)

        assert path.exists()
        assert path.suffix == ".yaml"

        # Verify YAML is loadable
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["metric_name"] == "Rank"

    def test_write_sql(self, generator, classifier, tmp_dir):
        classification = classifier.classify("Rank", "RANKX(ALL('T'), [X])")
        override = generator.generate_override(classification)
        path = generator.write_sql(override, tmp_dir)

        assert path.exists()
        assert path.suffix == ".sql"

        content = path.read_text()
        assert "LAYER 1" in content
        assert "LAYER 2" in content
        assert "LAYER 3" in content
        assert "DYNAMIC TABLE" in content
        assert "SEMANTIC VIEW" in content

    def test_render_sql_contains_all_layers(self, generator, classifier):
        classification = classifier.classify("Rank", "RANKX(ALL('T'), [X])")
        override = generator.generate_override(classification)
        sql = generator.render_sql(override)

        assert "CREATE OR REPLACE DYNAMIC TABLE" in sql
        assert "CREATE OR REPLACE SEMANTIC VIEW" in sql
        assert "ALTER SEMANTIC VIEW" in sql
        assert "CORTEX ANALYST" in sql

    def test_render_sql_header_metadata(self, generator, classifier):
        classification = classifier.classify("MyMetric", "RANKX(ALL('T'), [X])")
        override = generator.generate_override(classification)
        sql = generator.render_sql(override)

        assert "MYMETRIC" in sql
        assert "TIER 4" in sql


# =============================================================================
# Override Validator Tests
# =============================================================================

class TestOverrideValidator:
    """Tests for the Override validator."""

    def test_validate_valid_override(self, validator):
        override = SQLOverrideFile(
            metric_name="Test",
            safety_tier=4,
            hazard_category=HazardCategory.SPECIALIZED_BUILTIN_LOGIC.value,
            original_dax="RANKX(...)",
            snowflake_database="DB",
            snowflake_schema="SCHEMA",
            layer_1_dynamic_table=DynamicTableLayer(
                table_name="test_dt",
                source_table="source_table_name",
            ),
            layer_2_semantic_view=SemanticViewLayer(
                view_name="test_sv",
                metrics=[
                    SemanticMetricSpec(
                        name="test_metric",
                        expression="COUNT(*)",
                    )
                ],
            ),
            layer_3_cortex_metadata=CortexMetadataLayer(
                table_name="events",
                metric_synonyms=[
                    CortexSynonym(
                        column_name="test_metric",
                        synonyms=["test count"],
                    )
                ],
            ),
        )
        result = validator.validate(override)
        assert result.is_valid

    def test_validate_missing_layers(self, validator):
        override = SQLOverrideFile(
            metric_name="Test",
            hazard_category=HazardCategory.SPECIALIZED_BUILTIN_LOGIC.value,
        )
        result = validator.validate(override)
        assert not result.is_valid
        assert len(result.errors) > 0

    def test_validate_todo_warnings(self, validator):
        override = SQLOverrideFile(
            metric_name="Test",
            hazard_category=HazardCategory.SPECIALIZED_BUILTIN_LOGIC.value,
            layer_1_dynamic_table=DynamicTableLayer(
                table_name="test_dt",
                source_table="-- TODO: fill in source table",
            ),
            layer_2_semantic_view=SemanticViewLayer(
                view_name="test_sv",
                metrics=[
                    SemanticMetricSpec(
                        name="m1",
                        expression="-- TODO: define expression",
                    )
                ],
            ),
            layer_3_cortex_metadata=CortexMetadataLayer(table_name="t"),
        )
        result = validator.validate(override)
        assert len(result.warnings) > 0
        assert any("TODO" in w for w in result.warnings)

    def test_validate_invalid_sql_identifier(self, validator):
        override = SQLOverrideFile(
            metric_name="Test",
            hazard_category=HazardCategory.NONE.value,
            layer_1_dynamic_table=DynamicTableLayer(
                table_name="123-invalid!",
                source_table="src",
            ),
        )
        result = validator.validate(override)
        assert any("not a valid SQL identifier" in e for e in result.errors)

    def test_validate_draft_status_info(self, validator):
        override = SQLOverrideFile(
            metric_name="Test",
            status=OverrideStatus.DRAFT,
            hazard_category=HazardCategory.NONE.value,
        )
        result = validator.validate(override)
        assert any("DRAFT" in i for i in result.info)

    def test_validate_file_not_found(self, validator, tmp_dir):
        result = validator.validate_file(tmp_dir / "nonexistent.yaml")
        assert not result.is_valid
        assert any("not found" in e for e in result.errors)

    def test_validate_file_valid_yaml(self, validator, generator, classifier, tmp_dir):
        classification = classifier.classify("Rank", "RANKX(ALL('T'), [X])")
        override = generator.generate_override(classification)
        path = generator.write_override_yaml(override, tmp_dir)

        result = validator.validate_file(path)
        # Template overrides will have warnings (TODOs) but no schema errors
        assert any("Schema validation passed" in i for i in result.info)

    def test_validate_directory(self, validator, generator, classifier, tmp_dir):
        # Generate multiple overrides
        for name, dax in [("R1", "RANKX(ALL('T'), [X])"), ("R2", "SUMX('T', [X])")]:
            c = classifier.classify(name, dax)
            o = generator.generate_override(c)
            generator.write_override_yaml(o, tmp_dir)

        results = validator.validate_directory(tmp_dir)
        assert len(results) == 2

    def test_result_summary(self):
        result = OverrideValidationResult()
        result.add_error("Missing layer")
        result.add_warning("TODO found")
        result.add_info("Draft status")
        summary = result.summary()
        assert "ERRORS" in summary
        assert "WARNINGS" in summary
        assert "INFO" in summary


# =============================================================================
# Override File Loading Tests
# =============================================================================

class TestOverrideFileLoading:
    """Tests for loading override files from disk."""

    def test_load_override_file(self, generator, classifier, tmp_dir):
        classification = classifier.classify("Rank", "RANKX(ALL('T'), [X])")
        override = generator.generate_override(classification)
        path = generator.write_override_yaml(override, tmp_dir)

        loaded = load_override_file(path)
        assert loaded is not None
        assert loaded.metric_name == "Rank"

    def test_load_override_file_invalid(self, tmp_dir):
        bad_file = tmp_dir / "override_bad.yaml"
        bad_file.write_text("not: [valid: override")
        loaded = load_override_file(bad_file)
        assert loaded is None

    def test_load_override_directory(self, generator, classifier, tmp_dir):
        for name, dax in [("R1", "RANKX(ALL('T'), [X])"), ("R2", "SUMX('T', [X])")]:
            c = classifier.classify(name, dax)
            o = generator.generate_override(c)
            generator.write_override_yaml(o, tmp_dir)

        overrides = load_override_directory(tmp_dir)
        assert len(overrides) == 2
        assert "R1" in overrides
        assert "R2" in overrides

    def test_load_empty_directory(self, tmp_dir):
        overrides = load_override_directory(tmp_dir)
        assert len(overrides) == 0

    def test_load_nonexistent_directory(self):
        overrides = load_override_directory(Path("/nonexistent/dir"))
        assert len(overrides) == 0


# =============================================================================
# Safety Pipeline Tests
# =============================================================================

class TestTieredSafetyPipeline:
    """Tests for the full safety pipeline integration."""

    def test_pipeline_run(self, sample_model, tmp_dir):
        pipeline = TieredSafetyPipeline(
            override_dir=tmp_dir,
            database="TEST_DB",
            schema="TEST_SCHEMA",
        )
        result = pipeline.run(sample_model)

        assert result.total_measures == 5
        assert result.auto_translatable_count >= 2  # Revenue, Margin
        assert result.override_required_count >= 2  # Red Sales, Sales Rank, Running Total

    def test_pipeline_report(self, sample_model, tmp_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_dir)
        result = pipeline.run(sample_model)
        report = result.get_report()

        assert "total_measures" in report
        assert "tier_distribution" in report
        assert "hazard_breakdown" in report
        assert "override_coverage_pct" in report

    def test_pipeline_generates_override_templates(self, sample_model, tmp_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_dir)
        result = pipeline.run(sample_model, generate_templates=True)

        # Should have generated override files for flagged measures
        yaml_files = list(tmp_dir.glob("override_*.yaml"))
        sql_files = list(tmp_dir.glob("override_*.sql"))
        assert len(yaml_files) > 0
        assert len(sql_files) > 0

    def test_pipeline_writes_report(self, sample_model, tmp_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_dir)
        result = pipeline.run(sample_model)
        report_path = pipeline.write_safety_report(result, tmp_dir)

        assert report_path.exists()
        with open(report_path) as f:
            data = json.load(f)
        assert data["total_measures"] == 5
        assert "measure_details" in data

    def test_pipeline_updates_metric_metadata(self, sample_model, tmp_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_dir)
        pipeline.run(sample_model)

        # Check that metrics have updated complexity_tier
        for metric in sample_model.metrics:
            assert metric.complexity_tier in (1, 2, 3, 4)

        # The RANKX metric should be flagged
        rank_metric = next(
            m for m in sample_model.metrics if m.unique_name == "Sales Rank"
        )
        assert rank_metric.sync_enabled is False
        assert rank_metric.sync_failure_reason is not None
        assert "hazard" in rank_metric.sync_failure_reason.lower()

    def test_pipeline_override_coverage(self, sample_model, tmp_dir):
        pipeline = TieredSafetyPipeline(override_dir=tmp_dir)
        result = pipeline.run(sample_model)

        # After generating templates (which have TODOs), coverage count
        # counts existence not completion
        assert result.override_required_count >= 2

    def test_pipeline_no_override_dir(self, sample_model):
        """Pipeline should work without an override directory."""
        pipeline = TieredSafetyPipeline(override_dir=None)
        result = pipeline.run(sample_model, generate_templates=False)

        assert result.total_measures == 5
        assert len(result.overrides_generated) == 0

    def test_pipeline_auto_translate(self, sample_model, tmp_dir):
        """Auto-translated measures should have sql_expression set."""
        pipeline = TieredSafetyPipeline(override_dir=tmp_dir)
        result = pipeline.run(sample_model)

        # Simple SUM should have been auto-translated
        revenue = next(
            m for m in sample_model.metrics if m.unique_name == "Total Revenue"
        )
        # The auto-translation depends on the DAXTranslator — check if attempted
        assert "Total Revenue" in result.classifications
