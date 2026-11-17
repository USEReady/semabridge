"""UPSERT delta detection and merge (core/upsert_delta.py)."""

from semabridge.core.run_helpers import sync_mode_from_project_config
from semabridge.core.upsert_delta import (
    compute_delta,
    merge_upsert,
    normalize_model_name,
    normalize_name,
)
from semabridge.sml.models import (
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)


def _model(metrics, datasets=None, relationships=None):
    return SMLModel(
        unique_name="Sales",
        datasets=datasets if datasets is not None else [
            SMLDataset(unique_name="SalesFact", columns=[
                SMLColumn(unique_name="Units", data_type=DataType.INTEGER),
                SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
            ]),
        ],
        metrics=metrics,
        relationships=relationships or [],
    )


def _metric(name, expression, sql=None):
    return SMLMetric(unique_name=name, dataset="SalesFact", expression=expression, sql_expression=sql)


BASE_METRICS = [
    _metric("Total Units", "SUM([Units])"),
    _metric("Total Units YTD", "TOTALYTD([Total Units], 'Date'[Date])"),
    _metric("Sum of Revenue", "SUM('SalesFact'[Revenue])"),
]


def test_names_normalize_case_underscore_and_space():
    assert normalize_name("Total Units YTD") == normalize_name("TOTAL_UNITS_YTD") == "totalunitsytd"
    assert normalize_model_name("Competitive Marketing Analysis (More Synonyms)_updated") == normalize_model_name(
        "COMPETITIVE_MARKETING_ANALYSIS_MORE_SYNONYMS_UPDATED"
    )


def test_unchanged_reextraction_has_no_changes_even_if_derived_sql_differs():
    baseline = _model(BASE_METRICS).model_dump(mode="json")
    rerendered = [_metric(m.unique_name, m.expression, sql="SUM(X)") for m in BASE_METRICS]

    delta = compute_delta(_model(rerendered), baseline, baseline_snapshot_id="s1")

    assert delta["has_changes"] is False
    assert delta["kinds"]["metrics"]["unchanged"] == 3
    assert delta["summary_text"].startswith("No changes")


def test_added_modified_removed_and_summary_line():
    baseline = _model(BASE_METRICS).model_dump(mode="json")
    new = _model([
        _metric("TOTAL_UNITS", "SUM([Units])"),                     # same item, different spelling
        _metric("Total Units YTD", "TOTALYTD([Total Units], 'Date'[Date], \"6/30\")"),
        _metric("Avg Revenue per Unit", "DIVIDE(SUM(SalesFact[Revenue]), SUM(SalesFact[Units]))"),
    ])

    delta = compute_delta(new, baseline)
    metrics = delta["kinds"]["metrics"]

    assert [m["name"] for m in metrics["added"]] == ["Avg Revenue per Unit"]
    assert [m["name"] for m in metrics["modified"]] == ["Total Units YTD"]
    assert metrics["modified"][0]["old_expression"] == "TOTALYTD([Total Units], 'Date'[Date])"
    assert [m["name"] for m in metrics["removed"]] == ["Sum of Revenue"]
    assert metrics["unchanged"] == 1
    assert delta["has_changes"] is True
    assert delta["summary_text"] == (
        "1 added: Avg Revenue per Unit · 1 modified: Total Units YTD · "
        "1 removed from source (kept in target): Sum of Revenue · 2 unchanged"
    )


def test_merge_keeps_target_only_items_without_duplicates():
    baseline = _model(BASE_METRICS).model_dump(mode="json")
    new = _model([
        _metric("TOTAL_UNITS", "SUM([Units])"),
        _metric("Avg Revenue per Unit", "DIVIDE(1, 2)"),
    ], datasets=[SMLDataset(unique_name="SALESFACT", columns=[SMLColumn(unique_name="UNITS")])])

    merged = merge_upsert(new, baseline)

    assert [m.unique_name for m in merged.metrics] == [
        "TOTAL_UNITS", "Avg Revenue per Unit", "Total Units YTD", "Sum of Revenue",
    ]
    assert [d.unique_name for d in merged.datasets] == ["SALESFACT"]
    assert [c.unique_name for c in merged.datasets[0].columns] == ["UNITS", "Revenue"]


def test_relationship_and_table_changes_are_detected():
    rel = SMLRelationship(unique_name="REL_A", from_dataset="SalesFact", from_columns=["ProductID"],
                          to_dataset="Product", to_columns=["ProductID"])
    baseline = _model(BASE_METRICS, relationships=[rel]).model_dump(mode="json")
    changed_rel = rel.model_copy(update={"is_active": False})
    new = _model(BASE_METRICS, datasets=[
        SMLDataset(unique_name="SalesFact", columns=[
            SMLColumn(unique_name="Units", data_type=DataType.INTEGER),
            SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
            SMLColumn(unique_name="Discount", data_type=DataType.DECIMAL),
        ]),
    ], relationships=[changed_rel])

    delta = compute_delta(new, baseline)

    assert delta["kinds"]["relationships"]["modified"][0]["name"] == "REL_A"
    table_change = delta["kinds"]["tables"]["modified"][0]
    assert table_change["columns_added"] == ["Discount"]


def test_first_deployment_when_no_baseline():
    delta = compute_delta(_model(BASE_METRICS), None)
    assert delta["first_deployment"] is True
    assert delta["has_changes"] is True
    assert len(delta["kinds"]["metrics"]["added"]) == 3


def test_sync_mode_from_project_config():
    assert sync_mode_from_project_config("options:\n  write_strategy: upsert\n") == "upsert"
    assert sync_mode_from_project_config({"sync_mode": "COPY"}) == "copy"
    assert sync_mode_from_project_config({"options": {"write_strategy": "merge"}}) is None
    assert sync_mode_from_project_config("") is None


def test_annotate_delta_statuses_expressions_and_query_target():
    from semabridge.core.upsert_delta import annotate_delta

    baseline = _model([_metric("Total Units", "SUM([Units])"), _metric("Old Metric", "SUM([Units])")]).model_dump(mode="json")
    new = _model([
        _metric("Total Units", "SUM([Units]) * 2"),
        _metric("Revenue per Unit", "DIVIDE(SUM(SalesFact[Revenue]), SUM(SalesFact[Units]))"),
        _metric("Broken", "WEIRD()"),
    ])
    delta = compute_delta(new, baseline)
    ddl = """create or replace semantic view X
	tables (SALESFACT as DB.SCH.SALES_FACT)
	metrics (
		SALESFACT.TOTAL_UNITS as SUM(SALESFACT.UNITS::FLOAT) * 2,
		SALESFACT.REVENUE_PER_UNIT as (SUM(SALESFACT.REVENUE::FLOAT)) / NULLIF(SUM(SALESFACT.UNITS::FLOAT), 0),
		SALESFACT.BROKEN as CAST(NULL AS DOUBLE)
	)
	comment='x';"""
    dropped = [{"entity_name": "Broken", "reason": "placeholder: not translatable", "by_design": False}]

    annotate_delta(delta, new, dropped, deployed=True, deployed_ddl=ddl,
                   view={"database": "DB", "schema": "SCH", "name": "X"})

    metrics = delta["kinds"]["metrics"]
    added = {m["name"]: m for m in metrics["added"]}
    rpu = added["Revenue per Unit"]
    assert rpu["status"] == "deployed" and rpu["table"] == "SalesFact"
    assert rpu["expression"].startswith("DIVIDE(")
    assert rpu["sql_expression"].startswith("(SUM(SALESFACT.REVENUE")
    assert rpu["query"] == {"entity": "SALESFACT", "metric": "REVENUE_PER_UNIT"}
    assert added["Broken"]["status"] == "placeholder" and "query" not in added["Broken"]
    assert metrics["modified"][0]["query"]["metric"] == "TOTAL_UNITS"
    assert metrics["removed"][0]["status"] == "kept in Snowflake"
    assert delta["view"]["name"] == "X"
