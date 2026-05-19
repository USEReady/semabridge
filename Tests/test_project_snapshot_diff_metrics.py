from __future__ import annotations

from semabridge.api.services.project_runs_impl import _diff_models


def test_diff_models_includes_root_level_metrics_scoped_by_dataset():
    left_state = {
        "datasets": [{"unique_name": "continent 1", "columns": []}],
        "metrics": [
            {
                "unique_name": "MEASURE",
                "dataset": "continent 1",
                "expression": "COUNTROWS('continent 1')",
            }
        ],
    }
    right_state = {
        "datasets": [{"unique_name": "continent 1", "columns": []}],
        "metrics": [
            {
                "unique_name": "MEASURE",
                "dataset": "continent 1",
                "expression": "COUNTBLANK('continent 1'[Column1])",
            },
            {
                "unique_name": "MEASURE_21",
                "dataset": "continent 1",
                "expression": "COUNTROWS('continent 1')",
            },
        ],
    }

    rows = _diff_models(left_state, right_state)
    model_row = next(r for r in rows if r["name"] == "continent 1")

    measures = {m["name"]: m["status"] for m in model_row.get("measures", [])}
    assert measures["MEASURE"] == "MODIFIED"
    assert measures["MEASURE_21"] == "ADDED"
