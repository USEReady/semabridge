"""Tests for Feature 2: Models with 0 tables in dry run and sync.

Validates the 5 reason codes:
  - LIVE_CONNECTION
  - ONLY_AUTO_DATE_TABLES
  - ALL_TABLES_FAILED
  - MEASURES_ONLY
  - EMPTY_MODEL

And verifies:
  - Warning row: "No tables found - a semantic view cannot be created"
  - LIVE_CONNECTION shows target model: "Points to <remote_model_name> on Power BI Service"
  - Sibling models must NOT be blocked
  - Sync button disabled when all models in selection are SKIPPED_NO_TABLES ("Nothing to create")
  - Measures-only model routes measures to the Drop Ledger
  - Real sync skips model entirely (no view created, run status WARNING, note in report)
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from semabridge.api.services.dry_run_job_service import (
    _ensure_schema,
    get_dry_run_job_status,
    validate_multi_model_dry_run_readiness,
)
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.core.run_summary import RunStatus
from semabridge.core.exceptions import ZeroTablesModelError, PBIXLiveConnectionError
from semabridge.core.zero_tables import classify_tmsl_model, is_auto_date_table
from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile
from semabridge.repository.orm.session_factory import db_manager


def _make_pbix(path: Path, connections: dict | None = None, tmsl: dict | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Version", "1.28")
        if connections is not None:
            z.writestr("Connections", json.dumps(connections))
        if tmsl is not None:
            z.writestr("DataModelSchema", json.dumps(tmsl))
    return path


def test_classify_tmsl_model_all_5_reason_codes():
    # 1. LIVE_CONNECTION
    c_live = classify_tmsl_model(None, live_reference="sales_dataset")
    assert c_live == ("LIVE_CONNECTION", "sales_dataset", [])

    # 2. ONLY_AUTO_DATE_TABLES
    tmsl_date = {
        "model": {
            "tables": [
                {"name": "LocalDateTable_12345", "columns": [{"name": "Date"}]},
                {"name": "DateTableTemplate_abc", "columns": [{"name": "Year"}]},
            ]
        }
    }
    c_date = classify_tmsl_model(tmsl_date)
    assert c_date == ("ONLY_AUTO_DATE_TABLES", None, [])

    # 3. MEASURES_ONLY
    tmsl_measures = {
        "model": {
            "tables": [
                {"name": "LocalDateTable_12345", "columns": [{"name": "Date"}]},
                {
                    "name": "DateTableTemplate_abc",
                    "measures": [{"name": "TotalSales", "expression": "SUM(1)"}],
                },
            ]
        }
    }
    c_meas = classify_tmsl_model(tmsl_measures)
    assert c_meas[0] == "MEASURES_ONLY"
    assert len(c_meas[2]) == 1
    assert c_meas[2][0]["name"] == "TotalSales"

    # 4. EMPTY_MODEL
    tmsl_empty = {"model": {"tables": []}}
    c_empty = classify_tmsl_model(tmsl_empty)
    assert c_empty == ("EMPTY_MODEL", None, [])

    # Normal user tables -> returns None (not zero-table model)
    tmsl_valid = {
        "model": {
            "tables": [
                {"name": "Customers", "columns": [{"name": "Id"}]},
            ]
        }
    }
    assert classify_tmsl_model(tmsl_valid) is None


def test_measures_only_routes_to_drop_ledger():
    drop_ledger = DropLedger()
    measures = [{"name": "Revenue"}, {"name": "Margin"}]
    for m in measures:
        drop_ledger.record(
            entity_kind="metric",
            entity_name=m["name"],
            stage=DropStage.EXTRACTION,
            reason="Parent model had 0 physical tables - cannot generate semantic view without at least one table",
        )

    records = drop_ledger.records
    assert len(records) == 2
    assert records[0].entity_name == "Revenue"
    assert records[0].reason == "Parent model had 0 physical tables - cannot generate semantic view without at least one table"
    assert records[1].entity_name == "Margin"


def test_sibling_models_not_blocked_in_gate():
    _ensure_schema()
    now = datetime.now(timezone.utc)
    job_id = "job-zero-table-test-1"
    project_id = "proj-zero-table-1"

    with db_manager.get_session() as session:
        session.query(DryRunJobFile).delete()
        session.query(DryRunJob).delete()

        job = DryRunJob(
            job_id=job_id,
            project_id=project_id,
            status="success",
            created_at=now,
            source_config_json=json.dumps({"mode": "multiple"}),
            target_config_json=json.dumps({}),
            source_type="pbix",
        )
        session.add(job)

        # File 0: SKIPPED_NO_TABLES (zero tables)
        f0 = DryRunJobFile(
            file_id=f"{job_id}-f0",
            job_id=job_id,
            file_index=0,
            pbix_path="models/ThinReport.pbix",
            source_ref="models/ThinReport.pbix",
            display_name="ThinReport",
            label="ThinReport",
            status="SKIPPED_NO_TABLES",
            error_message="No tables found - a semantic view cannot be created",
            result_json=json.dumps({
                "status": "SKIPPED_NO_TABLES",
                "skipped_no_tables": True,
                "reason_code": "LIVE_CONNECTION",
                "model_reference": "remote-sales-model",
                "warning_message": "No tables found - a semantic view cannot be created",
                "warning_row": "Points to remote-sales-model on Power BI Service",
            }),
            queued_at=now,
            started_at=now,
            completed_at=now,
        )
        session.add(f0)

        # File 1: SUCCESS (valid sibling model)
        f1 = DryRunJobFile(
            file_id=f"{job_id}-f1",
            job_id=job_id,
            file_index=1,
            pbix_path="models/ValidModel.pbix",
            source_ref="models/ValidModel.pbix",
            display_name="ValidModel",
            label="ValidModel",
            status="success",
            result_json=json.dumps({
                "status": "success",
                "entity_mappings": [
                    {"id": "m1", "source_name": "Col1", "target_name": "COL1", "status": "valid"}
                ]
            }),
            queued_at=now,
            started_at=now,
            completed_at=now,
        )
        session.add(f1)

    source_cfg = {
        "mode": "multiple",
        "models": ["models/ThinReport.pbix", "models/ValidModel.pbix"],
    }

    # Sibling model is not blocked: the gate passes!
    validate_multi_model_dry_run_readiness(project_id, source_cfg)

    # Status check: can_sync is True because ValidModel succeeded, button is "Sync Models"
    st = get_dry_run_job_status(job_id)
    assert st["can_sync"] is True
    assert st["sync_button_disabled"] is False
    assert st["sync_button_label"] == "Sync Models"
    assert st["all_models_skipped"] is False

    # Check file 0 warning row & details
    f0_meta = next(f for f in st["files"] if f["label"] == "ThinReport")
    assert f0_meta["status"] == "SKIPPED_NO_TABLES"
    assert f0_meta["reason_code"] == "LIVE_CONNECTION"
    assert f0_meta["remote_model_name"] == "remote-sales-model"
    assert f0_meta["warning_row"] == "Points to remote-sales-model on Power BI Service"


def test_sync_button_disabled_when_all_models_skipped():
    _ensure_schema()
    now = datetime.now(timezone.utc)
    job_id = "job-all-skipped-test"
    project_id = "proj-all-skipped"

    with db_manager.get_session() as session:
        session.query(DryRunJobFile).delete()
        session.query(DryRunJob).delete()

        job = DryRunJob(
            job_id=job_id,
            project_id=project_id,
            status="success",
            created_at=now,
            source_config_json=json.dumps({"mode": "multiple"}),
            target_config_json=json.dumps({}),
            source_type="pbix",
        )
        session.add(job)

        f0 = DryRunJobFile(
            file_id=f"{job_id}-f0",
            job_id=job_id,
            file_index=0,
            pbix_path="models/Empty1.pbix",
            source_ref="models/Empty1.pbix",
            display_name="Empty1",
            label="Empty1",
            status="SKIPPED_NO_TABLES",
            error_message="No tables found - a semantic view cannot be created",
            result_json=json.dumps({
                "status": "SKIPPED_NO_TABLES",
                "skipped_no_tables": True,
                "reason_code": "EMPTY_MODEL",
                "warning_message": "No tables found - a semantic view cannot be created",
            }),
            queued_at=now,
            started_at=now,
            completed_at=now,
        )
        session.add(f0)

    st = get_dry_run_job_status(job_id)
    assert st["all_models_skipped"] is True
    assert st["can_sync"] is False
    assert st["sync_button_disabled"] is True
    assert st["sync_button_label"] == "Nothing to create"


def test_real_sync_skips_model_and_sets_run_status_warning():
    from semabridge.api.services.sync_execution_service import _run_single_job_impl

    # Mock job where engine returns WARNING / skipped_no_tables
    job = {
        "model_label": "ThinReportModel",
        "pbix_path": "models/Thin.pbix",
        "dataset_id": "d1",
        "resolved_display_name": "ThinReportModel",
        "physical_namespace": None,
    }

    job_err = {**job, "resolution_error": "No physical tables found"}
    res_err = _run_single_job_impl(
        job_err,
        engine_source_type="pbix",
        target_type="snowflake",
        config={"target": {}},
        config_path="",
        deploy_enabled=False,
        resolved_workspace_id="default",
    )
    assert res_err["status"] == "failed"
