"""The planner (Phase 3.6) on real .pbix models fed in as input.

Every .pbix in the folder named by SEMABRIDGE_GOLDEN_PBIX_DIR (the same
folder the golden tests use) goes through the Databricks conversion (Step 6)
and the planner, offline, in both CALCULATE modes. Checks: the self-check
passes, every measure is placed or left out with a reason, strict never
places more than approximate, and the plan is the same on every run.
Skipped when the folder is not set.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from test_snowflake_golden import _pbix_to_osi, hermetic  # noqa: F401  (same folder; hermetic fixture)

FOLDER = os.environ.get("SEMABRIDGE_GOLDEN_PBIX_DIR", "").strip()
MODELS = sorted(Path(FOLDER).glob("*.pbix")) if FOLDER and Path(FOLDER).is_dir() else []


@pytest.mark.skipif(not MODELS, reason="SEMABRIDGE_GOLDEN_PBIX_DIR is not set or holds no .pbix")
@pytest.mark.parametrize("pbix", MODELS, ids=[p.stem for p in MODELS])
def test_planner_on_real_model(pbix: Path, hermetic) -> None:  # noqa: F811
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views
    from semabridge.target_rules import DATABRICKS_RULES

    osi, rows = _pbix_to_osi(pbix)
    sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi, row_counts=rows)
    approx = plan_metric_views(sml)
    strict = plan_metric_views(sml, settings=PlannerSettings(calculate_mode="strict"))
    total = len(sml.metrics)
    for result in (approx, strict):
        s = result.as_dict()["summary"]
        assert s["measures_placed"] + s["measures_left_out"] == total
        assert all(lo.reason for lo in result.left_out)
    assert strict.as_dict()["summary"]["measures_placed"] <= approx.as_dict()["summary"]["measures_placed"]
    assert plan_metric_views(sml).as_dict() == approx.as_dict()
    print(f"{pbix.stem}: {approx.as_dict()['summary']} | strict placed {strict.as_dict()['summary']['measures_placed']}")
