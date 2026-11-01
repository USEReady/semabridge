import pytest
from unittest.mock import MagicMock
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric


def test_g7_null_metric_dependency_reason_propagation():
    """G7: When metric B depends on NULL placeholder metric A, B is emitted as NULL with COMMENT = 'depends on [A]: <reason>'."""
    sanitizer = MagicMock()
    sanitizer.to_alias = lambda x: x
    sanitizer.sanitize_column = lambda x: x
    translator = MagicMock()
    translator._normalize_string_boolean_comparisons = lambda x: x
    config = MagicMock()
    config.database = "DB"
    config.schema_name = "SCHEMA"
    builder = MetricsClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=MagicMock(),
        sanitizer=sanitizer,
        translator=translator,
        config=config,
    )

    metrics_lines = [
        '  SALES."METRIC_A" AS CAST(NULL AS DOUBLE) COMMENT = \'ranking / not translatable\'',
        '  SALES."METRIC_B" AS IF(SALES."METRIC_A" > 3, 1, 0)',
    ]
    metric_name_set = {"METRIC_A", "METRIC_B"}

    pruned = builder._prune_unresolved_metric_lines(metrics_lines, metric_name_set)
    b_line = next(l for l in pruned if 'METRIC_B' in l)
    assert "CAST(NULL AS DOUBLE)" in b_line
    assert "COMMENT =" in b_line
    assert "depends on [METRIC_A]: ranking / not translatable" in b_line


def test_g7_by_design_excluded_metrics_not_silently_dropped():
    """G7: By-design excluded metrics must be emitted as CAST(NULL AS DOUBLE) with COMMENT, not silently dropped."""
    sanitizer = MagicMock()
    sanitizer.to_alias = lambda x: x
    sanitizer.sanitize_column = lambda x: x
    translator = MagicMock()
    translator._normalize_string_boolean_comparisons = lambda x: x
    config = MagicMock()
    config.database = "DB"
    config.schema_name = "SCHEMA"
    builder = MetricsClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=MagicMock(),
        sanitizer=sanitizer,
        translator=translator,
        config=config,
    )

    metric = SMLMetric(
        unique_name="Top_10_Rank",
        dataset="Sales",
        expression="",
        sql_expression=None,
        sync_enabled=False,
        sync_failure_reason="BY_DESIGN_EXCLUDED: ranking / top-N metric",
    )
    sml = SMLModel(
        unique_name="TestModel",
        datasets=[SMLDataset(unique_name="Sales", columns=[SMLColumn(unique_name="Id")])],
        metrics=[metric],
    )
    dataset_aliases = {"Sales": "SALES"}
    lines = builder.build_for_sml(
        sml,
        dataset_aliases=dataset_aliases,
        dataset_by_name={"Sales": sml.datasets[0]},
        dataset_col_lookup={"Sales": {"Id"}},
        alias_by_raw={"Sales": "Sales"},
        all_physical_col_names={"ID"},
        emittable_metric_name_set=set(),
    )
    assert len(lines) == 1
    assert "Top_10_Rank" in lines[0]
    assert "CAST(NULL AS DOUBLE)" in lines[0]
    assert "COMMENT =" in lines[0]
    assert "ranking / top-N metric" in lines[0]
