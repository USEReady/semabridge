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


# project_runs_impl is a re-export shim (4203016 split it into
# mapping_service / run_service / snapshot_service / ... and project_shared).
# The functions read their globals from the module that defines them, so a
# patch on the shim alone is invisible to them: patch every module that has
# the name.
from semabridge.api.services import (  # noqa: E402
    folder_service as _folder_service,
    mapping_service as _mapping_service,
    project_shared as _project_shared,
    run_service as _run_service,
    schedule_service as _schedule_service,
    snapshot_service as _snapshot_service,
    version_service as _version_service,
)

_COMPAT_MODULES = (
    pri,
    _project_shared,
    _mapping_service,
    _run_service,
    _snapshot_service,
    _version_service,
    _folder_service,
    _schedule_service,
)


@pytest.fixture(autouse=True)
def _release_project_lease():
    """A run started here with a stubbed background runner never finishes,
    so its project lease would block the next test's run ("already
    running"). Release it after each test."""
    yield
    from semabridge.core import run_coordination

    run_coordination.release_leases(key=run_coordination.project_lease_key("project-1"))


def _patch_compat(monkeypatch, name, value):
    patched = False
    for module in _COMPAT_MODULES:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    assert patched, f"no compat module defines {name}"


@pytest.mark.asyncio
async def test_run_sync_injects_mappings_overrides_into_config(monkeypatch):
    """A SYNC run executes the project config with the user's saved mapping
    edits merged into mappings_overrides.

    Since 306915b the edits are read from the mapping_overrides table
    (project_config_store.list_overrides), not the JSON mapping store, and
    the run start passes a dry-run gate and duplicate-sync checks first;
    those are stubbed here -- this test is about the config handed to the run.
    """
    captured: dict = {}

    _patch_compat(monkeypatch, "_compat_projects", {"project-1": {"name": "proj", "source": "fabric"}})
    _patch_compat(
        monkeypatch,
        "_compat_project_configs",
        {
            "project-1": "project_name: proj\nsource:\n  type: fabric\ntarget:\n  type: snowflake\n",
        },
    )
    _patch_compat(monkeypatch, "_compat_load_modular_project", lambda project_id: None)

    from semabridge.api.services import project_config_store

    monkeypatch.setattr(
        project_config_store,
        "list_overrides",
        lambda project_id: [
            {
                "project_id": project_id,
                "source_path": "metrics.Revenue",
                "target_name": "REV_TOTAL",
                "entity_kind": "metric",
                "source_name": "Revenue",
            }
        ] if project_id == "project-1" else [],
    )

    async def fake_assert_sync_ready(project_id, config_override, force=False):
        return project_id

    async def fake_sync_safety_checks(project_id, config_override, payload, **kwargs):
        return {"sync_mode": "copy", "promoted_models": []}

    _patch_compat(monkeypatch, "_assert_sync_ready", fake_assert_sync_ready)
    _patch_compat(monkeypatch, "_sync_safety_checks", fake_sync_safety_checks)

    def fake_create_project_run(project_id, schedule_label="Manual", run_type="SYNC", project_cfg_override=None, **kwargs):
        captured["project_id"] = project_id
        captured["project_cfg_override"] = project_cfg_override
        return ({"id": "run-1", "project_id": project_id}, project_cfg_override or "", 0.0)

    _patch_compat(monkeypatch, "_create_project_run", fake_create_project_run)
    _patch_compat(monkeypatch, "_run_project_background", lambda *args, **kwargs: None)

    background_tasks = BackgroundTasks()
    result = await pri.run_project_now_compat("project-1", background_tasks, payload={"run_type": "SYNC"})

    # A new run is "queued" until it gets an execution slot.
    assert result["status"] == "queued"
    assert captured["project_id"] == "project-1"
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
    # A column override renames the semantic label only; the physical
    # unique_name stays (85eb508), extraction/CTAS paths depend on it.
    assert model.datasets[0].columns[0].label == "SALES_AMOUNT"
    assert model.datasets[0].columns[0].unique_name == "amount"


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


def test_execution_engine_disambiguates_overrides_colliding_after_sanitization(tmp_path: Path):
    # Regression test: "Total Units YTD Var" and "Total Units YTD Var %" both
    # sanitize to "TOTAL_UNITS_YTD_VAR" under _normalize_identifier_for_match.
    # Both metrics — and both overrides — must still resolve to their own,
    # distinct, correctly-mapped target names rather than one being dropped
    # and the other misassigned to the wrong source metric.
    model = SMLModel(
        unique_name="Model1",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[SMLColumn(unique_name="amount", data_type=DataType.DECIMAL)],
            )
        ],
        metrics=[
            SMLMetric(unique_name="Total Units YTD Var", dataset="Sales", expression="A-B"),
            SMLMetric(unique_name="Total Units YTD Var %", dataset="Sales", expression="DIVIDE(A-B, B)"),
        ],
    )

    cfg = tmp_path / "semabridge.yaml"
    cfg.write_text(
        "\n".join(
            [
                "project_name: test",
                "mappings_overrides:",
                "  - source_path: metrics.Total Units YTD Var",
                "    target_name: TOTAL_UNITS_YTD_VAR",
                "  - source_path: metrics.Total Units YTD Var %",
                "    target_name: TOTAL_UNITS_YTD_VAR_PCT",
            ]
        ),
        encoding="utf-8",
    )

    ExecutionEngine._apply_mapping_overrides_from_config(model, cfg)

    by_expression = {m.expression: m.unique_name for m in model.metrics}
    assert by_expression["A-B"] == "TOTAL_UNITS_YTD_VAR"
    assert by_expression["DIVIDE(A-B, B)"] == "TOTAL_UNITS_YTD_VAR_PCT"
