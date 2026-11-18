"""UPSERT and renames (core/upsert_delta.py).

A column rename (mapping override -> column.label) and a metric rename
(override -> unique_name, original_name keeps the source name) must show as
"modified" and deploy, and the old name must never be kept next to the new
one. A baseline written before the naming change 'Date' -> DATE (no
``semantic_naming_version``) registers as modified exactly once.
"""

import logging
from pathlib import Path

from semabridge.core.engine.config import _apply_mapping_overrides_from_config
from semabridge.core.upsert_delta import compute_delta, merge_upsert
from semabridge.repository.model_repository import _deserialize_sml_blob, _serialize_sml_blob
from semabridge.sml.models import (
    DataType,
    SMLAttribute,
    SMLColumn,
    SMLDataset,
    SMLDimension,
    SMLMetric,
    SMLModel,
)


def _model(metrics=None, columns=None, dimensions=None):
    return SMLModel(
        unique_name="Sales",
        datasets=[
            SMLDataset(unique_name="SalesFact", columns=columns if columns is not None else [
                SMLColumn(unique_name="Date", data_type=DataType.DATE),
                SMLColumn(unique_name="Region", data_type=DataType.STRING),
                SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
            ]),
        ],
        dimensions=dimensions or [],
        metrics=metrics if metrics is not None else [
            SMLMetric(unique_name="Total", dataset="SalesFact", expression="SUM([Amount])"),
            SMLMetric(unique_name="Count", dataset="SalesFact", expression="COUNTROWS(SalesFact)"),
        ],
    )


def _override(model, rows):
    logging.disable(logging.WARNING)
    try:
        _apply_mapping_overrides_from_config(
            model, Path("/nonexistent"),
            {"mappings_overrides": [{"source_path": p, "target_name": t} for p, t in rows],
             "target": {"type": "snowflake"}},
        )
    finally:
        logging.disable(logging.NOTSET)
    return model


def _snapshot(model):
    """What the repository stores and reads back for a deployed model."""
    return _deserialize_sml_blob(_serialize_sml_blob(model.model_dump(mode="json")))


# -- 1. column renames --------------------------------------------------------

def test_column_rename_is_modified_and_deploys():
    baseline = _snapshot(_model())
    new = _override(_model(), [("datasets.SalesFact.columns.Region", "SALES_REGION")])

    delta = compute_delta(new, baseline)

    assert delta["has_changes"] is True
    [table] = delta["kinds"]["tables"]["modified"]
    assert table["columns_renamed"] == [{"column": "region", "old_name": "REGION", "new_name": "SALES_REGION"}]
    assert "REGION renamed to SALES_REGION" in delta["summary_text"]


def test_unchanged_model_against_its_own_snapshot_has_no_changes():
    renamed = [("datasets.SalesFact.columns.Region", "SALES_REGION"), ("metrics.Total", "REVENUE")]
    deployed = merge_upsert(_override(_model(), renamed), _snapshot(_model()))

    for baseline in (_snapshot(deployed), deployed.model_dump(mode="json")):
        delta = compute_delta(_override(_model(), renamed), baseline)
        assert delta["has_changes"] is False, delta["summary_text"]


def test_baseline_before_naming_change_is_modified_exactly_once():
    legacy = _snapshot(_model())
    legacy.pop("semantic_naming_version")  # snapshot stored before the field existed (Date -> COL_DATE)

    first = compute_delta(_model(), legacy)
    assert first["has_changes"] is True
    [table] = first["kinds"]["tables"]["modified"]
    assert table["columns_renamed"] == [{"column": "date", "old_name": "COL_DATE", "new_name": "DATE"}]

    redeployed = _snapshot(merge_upsert(_model(), legacy))
    assert compute_delta(_model(), redeployed)["has_changes"] is False


def test_legacy_baseline_without_reserved_names_is_unchanged():
    model = _model(columns=[SMLColumn(unique_name="Region"), SMLColumn(unique_name="Amount")])
    legacy = _snapshot(model)
    legacy.pop("semantic_naming_version")

    assert compute_delta(model, legacy)["has_changes"] is False


def test_dimension_attribute_rename_is_modified():
    def dim(label):
        return [SMLDimension(unique_name="Geo", dataset="SalesFact", attributes=[
            SMLAttribute(unique_name="Region", label=label, dataset="SalesFact", dataset_column="Region"),
        ])]

    delta = compute_delta(_model(dimensions=dim("Sales Region")), _snapshot(_model(dimensions=dim("Region"))))

    assert [d["name"] for d in delta["kinds"]["dimensions"]["modified"]] == ["Geo"]


# -- 2. metric renames ---------------------------------------------------------

def test_metric_rename_is_one_modified_item_and_old_name_is_not_kept():
    baseline = _snapshot(_model())
    new = _override(_model(), [("metrics.Total", "REVENUE")])
    assert (new.metrics[0].unique_name, new.metrics[0].original_name) == ("REVENUE", "Total")

    delta = compute_delta(new, baseline)
    metrics = delta["kinds"]["metrics"]

    assert metrics["added"] == [] and metrics["removed"] == []
    [entry] = metrics["modified"]
    assert entry["renamed_from"] == "Total" and entry["name"] == "REVENUE"
    assert "1 modified: Total (renamed to REVENUE)" in delta["summary_text"]

    merged = merge_upsert(new, baseline)
    assert sorted(m.unique_name for m in merged.metrics) == ["Count", "REVENUE"]


def test_metric_renamed_again_replaces_previous_rename():
    baseline = _snapshot(_override(_model(), [("metrics.Total", "REVENUE")]))
    new = _override(_model(), [("metrics.Total", "NET_REVENUE")])

    delta = compute_delta(new, baseline)

    assert [m["renamed_from"] for m in delta["kinds"]["metrics"]["modified"]] == ["REVENUE"]
    assert delta["kinds"]["metrics"]["added"] == []
    assert sorted(m.unique_name for m in merge_upsert(new, baseline).metrics) == ["Count", "NET_REVENUE"]


def test_metric_rename_reverted_is_modified_and_drops_override_name():
    baseline = _snapshot(_override(_model(), [("metrics.Total", "REVENUE")]))
    new = _model()  # override removed: back to the source name

    delta = compute_delta(new, baseline)

    assert [m["name"] for m in delta["kinds"]["metrics"]["modified"]] == ["Total"]
    assert sorted(m.unique_name for m in merge_upsert(new, baseline).metrics) == ["Count", "Total"]


def test_baseline_only_metric_is_still_kept():
    baseline = _snapshot(_model())
    new = _model(metrics=[SMLMetric(unique_name="Total", dataset="SalesFact", expression="SUM([Amount])")])

    assert sorted(m.unique_name for m in merge_upsert(new, baseline).metrics) == ["Count", "Total"]


# -- 3. converter-renamed physical columns ------------------------------------

def test_baseline_column_is_not_kept_next_to_its_converter_renamed_self():
    baseline = _snapshot(_model(columns=[SMLColumn(unique_name="Order-Date")]))
    new = _model(columns=[
        SMLColumn(unique_name="Order Date"),
        SMLColumn(unique_name="Order-Date_2", renamed_from="Order-Date"),
    ])

    merged = merge_upsert(new, baseline)

    assert [c.unique_name for c in merged.datasets[0].columns] == ["Order Date", "Order-Date_2"]


def test_converter_renamed_baseline_column_is_not_kept_once_source_name_is_back():
    baseline = _snapshot(_model(columns=[
        SMLColumn(unique_name="Order Date"),
        SMLColumn(unique_name="Order-Date_2", renamed_from="Order-Date"),
    ]))
    assert baseline["datasets"][0]["columns"][1]["renamed_from"] == "Order-Date"  # survives the snapshot
    new = _model(columns=[SMLColumn(unique_name="Order Date"), SMLColumn(unique_name="Order-Date")])

    merged = merge_upsert(new, baseline)

    assert [c.unique_name for c in merged.datasets[0].columns] == ["Order Date", "Order-Date"]


def test_baseline_column_with_a_taken_deployed_name_is_not_kept():
    # Baseline 'Region' was deployed as SALES_REGION; the source dropped it
    # and has a column 'Sales Region' (also SALES_REGION). Keeping the
    # baseline column would deploy SALES_REGION twice.
    baseline = _snapshot(_override(_model(), [("datasets.SalesFact.columns.Region", "SALES_REGION")]))
    new = _model(columns=[SMLColumn(unique_name="Sales Region"), SMLColumn(unique_name="Amount"),
                          SMLColumn(unique_name="Date", data_type=DataType.DATE)])

    merged = merge_upsert(new, baseline)

    assert sorted(c.unique_name for c in merged.datasets[0].columns) == ["Amount", "Date", "Sales Region"]
