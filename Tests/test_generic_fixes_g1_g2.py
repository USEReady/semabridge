import pytest
from unittest.mock import MagicMock
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.ddl_builder import SemanticViewBuilder
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship


def test_g2_window_metric_emission_alias_pinned_to_inner_aggregate_table():
    """G2: A window metric must be on the same table as the metrics/aggregates inside its OVER clause (Snowflake 010263)."""
    sanitizer = MagicMock()
    sanitizer.to_alias = lambda x: x
    sanitizer.sanitize_column = lambda x: x
    builder = MetricsClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=MagicMock(),
        sanitizer=sanitizer,
        translator=MagicMock(),
        config=MagicMock(),
    )

    dataset_aliases = {"Sales": "SALES", "Store": "STORE", "Calendar": "CALENDAR"}
    fact_aliases = {"SALES"}

    # Window metric with PARTITION BY EXCLUDING dimension table STORE
    sql = 'SUM(SALES."RETURN_RATE_NUM"::FLOAT) OVER (PARTITION BY EXCLUDING STORE."TYPE", STORE."STORE") / NULLIF(SUM(SALES."RETURN_RATE_DEN"::FLOAT) OVER (PARTITION BY EXCLUDING STORE."TYPE", STORE."STORE"), 0)'
    
    # Even if default_alias is STORE, emission alias must resolve to SALES
    alias = builder._resolve_metric_emission_alias("STORE", sql, dataset_aliases, fact_aliases)
    assert alias == "SALES", f"Expected SALES but got {alias}"

    # Window metric over CALENDAR
    sql2 = 'SUM(SALES."TOTAL_COUNT") OVER (PARTITION BY EXCLUDING CALENDAR."MONTH")'
    alias2 = builder._resolve_metric_emission_alias("CALENDAR", sql2, dataset_aliases, fact_aliases)
    assert alias2 == "SALES", f"Expected SALES but got {alias2}"


def test_g1_g2_extract_referenced_table_aliases_excludes_partition_by():
    """G1/G2: _extract_referenced_table_aliases does not count excluded partition dimensions as aggregated tables."""
    sanitizer = MagicMock()
    builder = MetricsClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=MagicMock(),
        sanitizer=sanitizer,
        translator=MagicMock(),
        config=MagicMock(),
    )
    valid_aliases = {"SALES", "CALENDAR", "STORE"}
    sql = 'SUM(SALES."AMOUNT") OVER (PARTITION BY EXCLUDING CALENDAR."MONTH", STORE."TYPE")'
    refs = builder._extract_referenced_table_aliases(sql, valid_aliases)
    assert refs == {"SALES"}, f"Expected only SALES in refs, got {refs}"


def test_g1_reanchoring_measures_table_to_fact_table():
    """G1: A measure from a measure-only table is anchored on the fact whose columns it aggregates."""
    model = SMLModel(
        unique_name="test_model",
        datasets=[
            SMLDataset(unique_name="Orders", columns=[SMLColumn(unique_name="Amount")]),
            SMLDataset(unique_name="Calendar", columns=[SMLColumn(unique_name="Date")]),
            SMLDataset(unique_name="Analysis_DAX", columns=[]),
        ],
        relationships=[
            SMLRelationship(unique_name="Orders_Calendar", from_dataset="Orders", from_columns=["Date"], to_dataset="Calendar", to_columns=["Date"]),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Amount",
                dataset="Analysis_DAX",
                expression="SUM(Orders[Amount])",
            ),
            SMLMetric(
                unique_name="Total_Amount_Excl_Date",
                dataset="Analysis_DAX",
                expression="CALCULATE([Total_Amount], ALL(Calendar[Date]))",
            ),
        ],
    )
    sanitizer = MagicMock()
    sanitizer.to_alias = lambda x: x
    sanitizer.sanitize_column = lambda x: x
    builder = SemanticViewBuilder(
        config=MagicMock(),
        behavior=MagicMock(),
        identifier_sanitizer=sanitizer,
        live_schema_metadata={},
    )
    builder._reanchor_disconnected_fact_metrics(model)

    metric_by_name = {m.unique_name: m for m in model.metrics}
    assert metric_by_name["Total_Amount"].dataset == "Orders"
    assert metric_by_name["Total_Amount_Excl_Date"].dataset == "Orders"
