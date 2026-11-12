"""Per-row SAMEPERIODLASTYEAR (time intelligence "Option C").

The primary <X> SPLY metric follows each row's period -- a LAG window over the
date dimension's year, so grouping by year gives every year its previous
year's value -- while the <X> SPLY_LATEST companion keeps the
latest-vs-previous value anchored on the data's last date (enriched-view
flag). Both the initial translation and the deploy-time anchor-flag re-render
must keep that split; the re-render used to drop allow_window_enrichment and
turn every primary back into the fixed-anchor flag (HR golden 7/8).
"""
from __future__ import annotations

from semabridge.connectors.anchor_flag_rerender import rerender_anchor_dependent_metrics
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.sml.models import DataType, SMLColumn, SMLDataset, SMLMetric, SMLModel

_SPLY_DAX = "CALCULATE([New Hires],SAMEPERIODLASTYEAR('Date'[Date]))"


def _hr_model() -> SMLModel:
    return SMLModel(
        unique_name="hr",
        datasets=[
            SMLDataset(
                unique_name="Employee",
                source_table="EMPLOYEE",
                columns=[
                    SMLColumn(unique_name="IsNewHire", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Date", data_type=DataType.DATE),
                ],
            ),
            SMLDataset(
                unique_name="Date",
                source_table="DATE",
                columns=[
                    SMLColumn(unique_name="Date", data_type=DataType.DATE, is_key=True),
                    SMLColumn(unique_name="Year", data_type=DataType.INTEGER),
                ],
            ),
        ],
        metrics=[
            SMLMetric(unique_name="New Hires", dataset="Employee", expression="SUM(Employee[IsNewHire])",
                      sql_expression='SUM(EMPLOYEE."ISNEWHIRE")'),
            SMLMetric(unique_name="New Hires SPLY", dataset="Employee", expression=_SPLY_DAX),
            SMLMetric(unique_name="New Hires SPLY_LATEST", dataset="Employee", expression=_SPLY_DAX),
        ],
    )


def _translate(model: SMLModel, name: str, **kwargs):
    return DAXTranslator().translate(
        _SPLY_DAX, "EMPLOYEE", "Employee",
        metric_name=name, metrics_context=model.metrics, datasets=model.datasets,
        relationships=[], skip_tier5=True, **kwargs,
    )


def test_primary_sply_is_a_per_row_window_over_the_year():
    model = _hr_model()

    res = _translate(model, "New Hires SPLY", allow_window_enrichment=True)

    assert res.is_success
    assert res.sql.startswith("LAG(")
    assert 'OVER (ORDER BY COL_DATE."YEAR")' in res.sql
    assert "MAX_DATE" not in res.sql and "IS_SPLY" not in res.sql


def test_latest_companion_stays_anchored_on_the_data():
    model = _hr_model()

    res = _translate(
        model, "New Hires SPLY_LATEST",
        allow_window_enrichment=False, anchor_flag_map={("SPLY_YEAR",): "IS_SPLY_YEAR"},
    )

    assert res.is_success
    assert "IS_SPLY_YEAR" in res.sql
    assert "LAG(" not in res.sql


def test_anchor_flag_rerender_keeps_primary_window_and_flags_only_the_companion():
    model = _hr_model()
    primary, latest = model.metrics[1], model.metrics[2]
    primary.sql_expression = _translate(model, primary.unique_name, allow_window_enrichment=True).sql
    latest.sql_expression = _translate(model, latest.unique_name).sql
    lookup = {ds.unique_name: {c.unique_name.upper() for c in ds.columns} for ds in model.datasets}
    aliases = {ds.unique_name: ds.unique_name.upper() for ds in model.datasets}
    primary_sql = primary.sql_expression

    rerender_anchor_dependent_metrics(
        model, {"employee": {("SPLY_YEAR",): "IS_SPLY_YEAR"}}, lookup, aliases, label="confirmed"
    )

    assert primary.sql_expression == primary_sql
    assert "IS_SPLY_YEAR" not in primary.sql_expression
    assert "IS_SPLY_YEAR" in latest.sql_expression
