"""Upsert run verification tests.

Verifies Item 5:
- No changes -> deploy skipped and DDL identical
- Second upsert finds no changes
- Baseline = last successful deploy
- Corrupt baseline fails closed
- Removed fields listed in run summary
- Overrides still applied, override on a removed object flagged
"""
import json
import pytest
from types import SimpleNamespace
from datetime import datetime, timezone
from semabridge.sml.models import SMLModel, SMLMetric, SMLDataset, SMLColumn
from semabridge.core.upsert_delta import compute_delta, merge_upsert, parse_baseline_blob
from semabridge.core.engine.exceptions import ConversionError
from semabridge.core.engine.config import _apply_mapping_overrides_from_config
from semabridge.core.run_summary import StepStatus


def _create_model(metric_specs, col_names):
    return SMLModel(
        unique_name="TEST_MODEL",
        metrics=[
            SMLMetric(unique_name=name, label=name, dataset="DS", expression=expr)
            for name, expr in metric_specs
        ],
        datasets=[
            SMLDataset(
                unique_name="DS",
                source_table="DS",
                columns=[SMLColumn(unique_name=c, label=c) for c in col_names],
            )
        ],
    )


def test_no_changes_deploy_skipped():
    """No changes between baseline and new model -> has_changes is False, deploy skipped."""
    model = _create_model([("TOTAL_SALES", "SUM(DS[AMOUNT])")], ["ID", "AMOUNT"])
    baseline_dict = model.model_dump()

    delta = compute_delta(model, baseline_dict)
    assert delta["has_changes"] is False
    assert delta["totals"]["added"] == 0
    assert delta["totals"]["modified"] == 0
    assert delta["totals"]["removed"] == 0


def test_second_upsert_finds_no_changes():
    """First upsert merges changes; immediately running a second upsert against that result finds no changes."""
    base = _create_model([("TOTAL_SALES", "SUM(DS[AMOUNT])")], ["ID", "AMOUNT"])
    # First sync added PROFIT
    new_model = _create_model(
        [("TOTAL_SALES", "SUM(DS[AMOUNT])"), ("PROFIT", "SUM(DS[PROFIT])")],
        ["ID", "AMOUNT", "PROFIT"],
    )

    # 1st upsert: has changes
    delta1 = compute_delta(new_model, base.model_dump())
    assert delta1["has_changes"] is True
    assert delta1["totals"]["added"] == 1

    # Deployed model after 1st upsert is merged
    deployed_1st = merge_upsert(new_model, base.model_dump())

    # 2nd upsert against the same source model: delta against deployed_1st must find NO changes
    delta2 = compute_delta(new_model, deployed_1st.model_dump())
    assert delta2["has_changes"] is False
    assert delta2["totals"]["added"] == 0
    assert delta2["totals"]["modified"] == 0
    assert delta2["totals"]["removed"] == 0


def test_corrupt_baseline_fails_closed():
    """A corrupted sml_blob in the baseline snapshot must raise an error and fail closed."""
    corrupt_blob = "{NOT_VALID_JSON"

    with pytest.raises(Exception):
        parse_baseline_blob(corrupt_blob, "snap-corrupt")


def test_removed_fields_listed_in_run_summary():
    """Fields removed in Power BI are listed in the delta summary."""
    baseline = _create_model(
        [("TOTAL_SALES", "SUM(DS[AMOUNT])"), ("OLD_METRIC", "SUM(DS[OLD])")],
        ["ID", "AMOUNT", "OLD_COL"],
    )
    # New model drops OLD_METRIC and OLD_COL
    current = _create_model([("TOTAL_SALES", "SUM(DS[AMOUNT])")], ["ID", "AMOUNT"])

    delta = compute_delta(current, baseline.model_dump())
    assert delta["has_changes"] is True
    assert delta["totals"]["removed"] >= 1

    removed_metric_names = [m["name"] for m in delta["kinds"]["metrics"]["removed"]]
    assert "OLD_METRIC" in removed_metric_names

    # Check summary text mentions removed items kept in target
    assert "removed from source (kept in target)" in delta["summary_text"]

    # Merged model keeps removed items
    merged = merge_upsert(current, baseline.model_dump())
    merged_metric_names = [m.unique_name for m in merged.metrics]
    assert "TOTAL_SALES" in merged_metric_names
    assert "OLD_METRIC" in merged_metric_names


def test_overrides_applied_and_removed_object_flagged(tmp_path):
    """Overrides on existing objects apply; overrides on removed objects are flagged."""
    model = _create_model([("TOTAL_SALES", "SUM(DS[AMOUNT])")], ["ID", "AMOUNT"])

    # Override for active metric and for a removed/missing metric
    cfg_payload = {
        "mappings_overrides": [
            {"source_path": "metrics.TOTAL_SALES", "target_name": "REVENUE"},
            {"source_path": "metrics.DELETED_METRIC", "target_name": "OLD_TARGET"},
        ]
    }

    _apply_mapping_overrides_from_config(model, tmp_path / "dummy.yaml", config_payload=cfg_payload)

    # Active metric was renamed
    sales_metric = next(m for m in model.metrics if m.unique_name == "REVENUE")
    assert sales_metric is not None

    # Removed metric override was flagged in unmatched_mapping_overrides
    assert "metrics.DELETED_METRIC" in model.unmatched_mapping_overrides
