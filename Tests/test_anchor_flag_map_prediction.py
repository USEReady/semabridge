"""Regression tests for Stage 2 of the anchor_flag_map wiring fix:
`predict_anchor_flag_map` (converter/time_intelligence_shapes.py and
SnowflakeEmitter) and its consistency with the real, live-connection-based
`_create_enriched_view` population of `translator.anchor_flag_map`.

See semabridge_max_date_root_cause_confirmed / semabridge_max_date_stage2
memory notes for the full design this implements.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.converter.time_intelligence_shapes import (
    discover_time_intelligence_shapes,
    metrics_with_time_intelligence_shapes,
    predict_anchor_flag_map,
)
from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel, DataType


def _build_emitter(auto_create_enriched_view: bool = True) -> SnowflakeEmitter:
    config = SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",
        warehouse="test_wh",
        database="test_db",
        schema_name="test_schema",
        role="test_role",
    )
    behavior = ConnectorBehavior()
    behavior.snowflake.auto_execute_precompute = True
    behavior.snowflake.auto_create_enriched_view = auto_create_enriched_view
    behavior.snowflake.use_enriched_view_for_metrics = True
    return SnowflakeEmitter(config, behavior)


def _model_with_ytd_metric() -> SMLModel:
    return SMLModel(
        unique_name="synthetic_model",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Date", data_type=DataType.DATE),
                ],
            ),
            SMLDataset(
                unique_name="Date",
                source_table="DATE_DIM",
                columns=[SMLColumn(unique_name="Date", data_type=DataType.DATE)],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Units_YTD",
                dataset="SalesFact",
                expression="TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Pure function: converter/time_intelligence_shapes.py::predict_anchor_flag_map
# ---------------------------------------------------------------------------

def test_predict_anchor_flag_map_empty_when_no_shapes():
    metrics = [SimpleNamespace(unique_name="Plain_Sum", expression="SUM('SalesFact'[Units])")]
    assert predict_anchor_flag_map(metrics, ["SalesFact"]) == {}


def test_predict_anchor_flag_map_builds_nested_map_for_every_eligible_table():
    metrics = [
        SimpleNamespace(
            unique_name="Total_Units_YTD",
            expression="TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
        )
    ]
    result = predict_anchor_flag_map(metrics, ["SalesFact", "OtherFact"])
    assert result == {
        "salesfact": {("YTD",): "IS_YTD"},
        "otherfact": {("YTD",): "IS_YTD"},
    }


def test_predict_anchor_flag_map_no_entry_for_ineligible_tables():
    """A fact table simply absent from `eligible_fact_tables` gets no
    entry at all -- mirrors _create_enriched_view's own guarantee that a
    table whose anchor couldn't be established gets no flag columns."""
    metrics = [
        SimpleNamespace(
            unique_name="Total_Units_YTD",
            expression="TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
        )
    ]
    result = predict_anchor_flag_map(metrics, [])
    assert result == {}


def test_metrics_with_time_intelligence_shapes_aggregates_to_discover_result():
    """discover_time_intelligence_shapes must remain the union of values
    from the new per-metric function -- its public contract is unchanged
    by the Stage 2 refactor."""
    metrics = [
        SimpleNamespace(unique_name="A", expression="TOTALYTD(SUM('F'[X]), 'Date'[Date])"),
        SimpleNamespace(unique_name="B", expression="SUM('F'[Y])"),
        SimpleNamespace(
            unique_name="C",
            expression="CALCULATE(SUM('F'[Z]), SAMEPERIODLASTYEAR('Date'[Date]))",
        ),
    ]
    per_metric = metrics_with_time_intelligence_shapes(metrics)
    assert per_metric == {"A": ("YTD",), "C": ("SPLY_YEAR",)}
    assert set(per_metric.values()) == discover_time_intelligence_shapes(metrics)


# ---------------------------------------------------------------------------
# SnowflakeEmitter.predict_anchor_flag_map -- no live connection
# ---------------------------------------------------------------------------

def test_emitter_predict_with_no_connection_uses_model_declared_columns(monkeypatch):
    emitter = _build_emitter()
    model = _model_with_ytd_metric()
    monkeypatch.setattr(emitter, "_find_date_table", lambda m: ("Date", "Date", "Date"))

    # No cursor was ever attached to this emitter -- _live_schema_metadata
    # is empty, so every column check must fall back to the model's own
    # declared columns.
    predicted = emitter.predict_anchor_flag_map(model)

    assert predicted == {"salesfact": {("YTD",): "IS_YTD"}}


def test_emitter_predict_returns_empty_when_auto_create_enriched_view_disabled(monkeypatch):
    """If enrichment is configured OFF for this project,
    _create_enriched_view never runs on a real deploy either -- predicting
    a flag column here would promise something that will never exist."""
    emitter = _build_emitter(auto_create_enriched_view=False)
    model = _model_with_ytd_metric()
    monkeypatch.setattr(emitter, "_find_date_table", lambda m: ("Date", "Date", "Date"))

    assert emitter.predict_anchor_flag_map(model) == {}


def test_emitter_predict_returns_empty_when_no_date_table(monkeypatch):
    emitter = _build_emitter()
    model = _model_with_ytd_metric()
    # No date-table relationship at all for this model.
    monkeypatch.setattr(emitter, "_find_date_table", lambda m: None)
    assert emitter.predict_anchor_flag_map(model) == {}


# ---------------------------------------------------------------------------
# Consistency: predicted map (no connection) vs real map (from an actual
# _create_enriched_view call against a mocked cursor) — the core Stage 2
# guarantee: naming can never drift between the two, only eligibility can.
# ---------------------------------------------------------------------------

def test_predicted_map_matches_real_create_enriched_view_map():
    emitter = _build_emitter()
    model = _model_with_ytd_metric()

    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = ("2024-01-01",)

    def _fake_find_date_table(m):
        return ("Date", "Date", "Date")

    emitter._find_date_table = _fake_find_date_table  # type: ignore[method-assign]

    predicted = emitter.predict_anchor_flag_map(model)
    assert predicted, "sanity: this scenario should predict a flag column"

    # Real run: same emitter, same model, but a live (mocked) cursor —
    # _create_enriched_view populates emitter.translator.anchor_flag_map
    # for real via the exact code path Step 9 uses.
    for fact_table in emitter._get_fact_tables_needing_enrichment(model):
        emitter._create_enriched_view(model, fake_cursor, fact_table=fact_table)

    assert emitter.translator.anchor_flag_map == predicted, (
        "predicted (no connection) and real (live cursor) anchor_flag_map "
        "must be byte-for-byte identical for the same model — the whole "
        "point of reusing discover_time_intelligence_shapes/"
        "flag_column_name in both places"
    )


def test_predicted_map_matches_real_map_across_multiple_shapes():
    """Same consistency guarantee, now with two distinct shapes on the
    same fact table (YTD and SPLY_YEAR) — both must independently agree."""
    emitter = _build_emitter()
    model = SMLModel(
        unique_name="synthetic_model_2",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SALES_FACT",
                columns=[
                    SMLColumn(unique_name="Units", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Date", data_type=DataType.DATE),
                ],
            ),
            SMLDataset(
                unique_name="Date",
                source_table="DATE_DIM",
                columns=[SMLColumn(unique_name="Date", data_type=DataType.DATE)],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Units_YTD",
                dataset="SalesFact",
                expression="TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
            ),
            SMLMetric(
                unique_name="Total_Units_SPLY",
                dataset="SalesFact",
                expression="CALCULATE(SUM('SalesFact'[Units]), SAMEPERIODLASTYEAR('Date'[Date]))",
            ),
        ],
    )
    emitter._find_date_table = lambda m: ("Date", "Date", "Date")  # type: ignore[method-assign]

    predicted = emitter.predict_anchor_flag_map(model)
    assert predicted == {
        "salesfact": {("YTD",): "IS_YTD", ("SPLY_YEAR",): "IS_SPLY_YEAR"}
    }

    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = ("2024-01-01",)
    for fact_table in emitter._get_fact_tables_needing_enrichment(model):
        emitter._create_enriched_view(model, fake_cursor, fact_table=fact_table)

    assert emitter.translator.anchor_flag_map == predicted
