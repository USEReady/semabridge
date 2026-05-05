from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest
from fastapi import BackgroundTasks


os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_mapping_overrides_apply_to_sync.sqlite")

if "psycopg2" not in sys.modules:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_stub.__version__ = "2.9.9"
    psycopg2_stub.apilevel = "2.0"
    psycopg2_stub.threadsafety = 2
    psycopg2_stub.paramstyle = "pyformat"
    psycopg2_stub.Error = Exception
    psycopg2_stub.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.api.services import project_runs_impl as pri
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.sml.models import DataType, SMLColumn, SMLDataset, SMLMetric, SMLModel


@pytest.mark.asyncio
async def test_run_sync_injects_mappings_overrides_into_config(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(pri, "_compat_projects", {"project-1": {"name": "proj", "source": "fabric"}})
    monkeypatch.setattr(
        pri,
        "_compat_project_configs",
        {
            "project-1": "project_name: proj\nsource:\n  type: fabric\ntarget:\n  type: snowflake\n",
        },
    )
    monkeypatch.setattr(
        pri,
        "_compat_mappings",
        {
            "m-1": {
                "id": "m-1",
                "project_id": "project-1",
                "source_path": "metrics.Revenue",
                "target_name": "REV_TOTAL",
                "entity_kind": "metric",
                "source_name": "Revenue",
                "is_user_edited": True,
            }
        },
    )

    def fake_create_project_run(project_id, schedule_label="Manual", run_type="SYNC", project_cfg_override=None, restore_snapshot_id=None):
        captured["project_id"] = project_id
        captured["project_cfg_override"] = project_cfg_override
        return ({"id": "run-1", "project_id": project_id}, project_cfg_override or "", 0.0)

    monkeypatch.setattr(pri, "_create_project_run", fake_create_project_run)

    background_tasks = BackgroundTasks()
    result = await pri.run_project_now_compat("project-1", background_tasks, payload={"run_type": "SYNC"})

    assert result["status"] == "running"
    cfg = str(captured.get("project_cfg_override") or "")
    assert "mappings_overrides:" in cfg
    assert "source_path: metrics.Revenue" in cfg
    assert "target_name: REV_TOTAL" in cfg


def test_execution_engine_applies_mapping_overrides_from_config(tmp_path: Path):
    model = SMLModel(
        unique_name="Model1",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                ],
            )
        ],
        metrics=[
            SMLMetric(unique_name="Revenue", dataset="Sales", expression="SUM([amount])"),
        ],
    )

    cfg = tmp_path / "semabridge.yaml"
    cfg.write_text(
        "\n".join(
            [
                "project_name: test",
                "mappings_overrides:",
                "  - source_path: metrics.Revenue",
                "    target_name: REV_TOTAL",
                "  - source_path: datasets.Sales.columns.amount",
                "    target_name: SALES_AMOUNT",
            ]
        ),
        encoding="utf-8",
    )

    ExecutionEngine._apply_mapping_overrides_from_config(model, cfg)

    assert model.metrics[0].unique_name == "REV_TOTAL"
    assert model.datasets[0].columns[0].unique_name == "SALES_AMOUNT"


def test_execution_engine_applies_metric_override_with_space_underscore_variants(tmp_path: Path):
    model = SMLModel(
        unique_name="Model1",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                ],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="MEASURE_2",
                label="Measure 2",
                dataset="Sales",
                expression="SUM([amount])",
            ),
        ],
    )

    cfg = tmp_path / "semabridge.yaml"
    cfg.write_text(
        "\n".join(
            [
                "project_name: test",
                "mappings_overrides:",
                "  - source_path: metrics.Measure 2",
                "    target_name: MEASURE_FF",
            ]
        ),
        encoding="utf-8",
    )

    ExecutionEngine._apply_mapping_overrides_from_config(model, cfg)

    assert model.metrics[0].unique_name == "MEASURE_FF"
