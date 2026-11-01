import pytest
from unittest.mock import MagicMock
from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLRelationship, SMLMetric, Cardinality


def test_resolve_physical_column_name_does_not_prematurely_borrow_partner_column():
    """Verify that a column present on the dataset (e.g. ProductID) is not overridden
    by a partner's relationship column (e.g. RightItemSetId)."""
    sanitizer = MagicMock()
    sanitizer.sanitize_column = lambda x: x.upper()
    sanitizer.sanitize_table_name = lambda x: x.upper()
    schema_mgr = SnowflakeSchemaManager(MagicMock(), MagicMock(), sanitizer, MagicMock())
    schema_mgr._live_schema_metadata = {}
    schema_mgr._sanitize_col_name = lambda x: x.upper()

    ds_target = SMLDataset(
        unique_name="Associated Product",
        columns=[
            SMLColumn(unique_name="ProductID", is_key=True),
            SMLColumn(unique_name="Product"),
        ]
    )
    ds_source = SMLDataset(
        unique_name="Association",
        columns=[
            SMLColumn(unique_name="RightItemSetId"),
        ]
    )
    rel = SMLRelationship(
        unique_name="Assoc_Rel",
        from_dataset="Association",
        from_columns=["RightItemSetId"],
        to_dataset="Associated Product",
        to_columns=["ProductID"],
        cardinality=Cardinality.MANY_TO_ONE,
        is_active=True,
    )
    model = SMLModel(
        unique_name="TestModel",
        datasets=[ds_target, ds_source],
        relationships=[rel],
    )

    resolved = schema_mgr._resolve_physical_column_name(ds_target, "ProductID", model=model)
    assert resolved == "PRODUCTID", f"Expected 'PRODUCTID', got '{resolved}'"


def test_compile_time_constant_with_content_is_retained_as_null():
    """Verify that non-empty constant measures (e.g. image URLs) are retained as NULL placeholders,
    not dropped completely."""
    from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
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
        unique_name="Info_Tooltip",
        dataset="Details",
        expression='"https://example.com/img.png"',
        sql_expression=None,
        sync_enabled=False,
        sync_failure_reason="BY_DESIGN_EXCLUDED: expression has no column/measure/table reference (a compile-time constant)",
    )
    sml = SMLModel(
        unique_name="TestModel",
        datasets=[SMLDataset(unique_name="Details", columns=[SMLColumn(unique_name="Id")])],
        metrics=[metric],
    )
    dataset_aliases = {"Details": "DETAILS"}
    lines = builder.build_for_sml(
        sml,
        dataset_aliases=dataset_aliases,
        dataset_by_name={"Details": sml.datasets[0]},
        dataset_col_lookup={"Details": {"Id"}},
        alias_by_raw={"Details": "Details"},
        all_physical_col_names={"ID"},
        emittable_metric_name_set=set(),
    )
    assert len(lines) == 1
    assert "Info_Tooltip" in lines[0]
    assert "CAST(NULL AS DOUBLE)" in lines[0]
