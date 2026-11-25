"""Golden gate for the Databricks target: the Unity Catalog metric view SQL
generated from the pinned PBIX / Fabric / Snowflake-source SML snapshots.

Offline (no SQL is executed). Any difference fails. Regenerate ONLY for an
intended, reviewed output change:
    SEMABRIDGE_UPDATE_GOLDEN=1 python -m pytest Tests/characterization/test_databricks_snapshots.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import SMLModel

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"
SOURCES = ["pbix", "fabric", "snowflake"]


def _generate(source: str) -> str:
    model = SMLModel.model_validate(json.loads((SNAPSHOT_DIR / f"{source}.sml.json").read_text(encoding="utf-8")))
    pub = DatabricksPublisher(
        DatabricksConfig(host="example.cloud.databricks.com", token="offline", warehouse_id="wh",
                         catalog="main", schema_name="analytics"),
        behavior=ConnectorBehavior(databricks=DatabricksBehavior(measure_view_type="metric_view")),
    )
    pub._offline_plan_mode = True
    statements, created, skipped, details = pub.generate_measure_view_statements(model, view_type_override="metric_view")
    header = f"-- views created: {created}, skipped: {skipped}\n"
    skipped_lines = "".join(
        f"-- skipped: {d.get('name') or d.get('view') or ''}: {d.get('reason') or ''}\n" for d in details
    )
    return header + skipped_lines + "\n\n".join(s.strip() for s in statements) + "\n"


@pytest.mark.parametrize("source", SOURCES)
def test_databricks_metric_view_sql_unchanged(source: str) -> None:
    path = SNAPSHOT_DIR / f"{source}.databricks.sql"
    actual = _generate(source)
    if os.getenv("SEMABRIDGE_UPDATE_GOLDEN") == "1":
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"missing snapshot {path.name}; run once with SEMABRIDGE_UPDATE_GOLDEN=1"
    expected = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert actual == expected, f"{path.name} changed: Databricks metric view output is no longer identical"
