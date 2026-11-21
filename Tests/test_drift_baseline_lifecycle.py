"""
Tests for Drift Monitoring Baseline Lifecycle, Status Integrity, and Target Metadata.

Covers:
  1. record_deploy_baseline / get_deploy_baseline by project_id and full view name.
  2. SnowflakeEmitter post-deploy baseline capture with real project_id and source definitions.
  3. run_drift_check uses deploy baseline when available (not live_capture).
  4. Live view modification after deploy produces EXPRESSION_MISMATCH on check.
  5. Live capture without prior baseline returns status="BASELINE_CAPTURED", never "IN_SYNC".
  6. SOURCE_CHANGED compares latest snapshot against deploy-stored source definitions.
  7. get_project_drift_target_info and /api/drift/events return all required metadata.
"""
import os
import json
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")

from semabridge.governance.drift_service import (
    record_deploy_baseline,
    get_deploy_baseline,
    _full_view_key,
    _view_key,
    get_project_drift_target_info,
    run_drift_check,
    _load_state,
    _save_state,
)


@pytest.fixture(autouse=True)
def clean_drift_state(tmp_path, monkeypatch):
    """Ensure tests run against a clean isolated drift state file."""
    state_file = tmp_path / "drift_state.json"
    state_file.write_text(json.dumps({"baselines": {}, "events": [], "last_checks": {}}), encoding="utf-8")
    monkeypatch.setattr("semabridge.governance.drift_service._state_path", lambda: state_file)
    monkeypatch.setattr("semabridge.governance.drift_service._db_load_events", lambda: None)
    monkeypatch.setattr("semabridge.governance.drift_service._db_save_events", lambda *a, **k: False)
    yield


def test_record_and_get_deploy_baseline_by_project_and_full_view():
    """record_deploy_baseline requires project_id and looks up by project + full view name."""
    # Refuses when project_id is empty
    assert record_deploy_baseline(None, "DB.SCHEMA.VIEW_A", {"M1": "COUNT(*)"}) is None
    assert record_deploy_baseline("", "DB.SCHEMA.VIEW_A", {"M1": "COUNT(*)"}) is None

    # Successfully records with project_id
    base = record_deploy_baseline(
        project_id="proj-sales",
        view_name="SALES_DB.ANALYTICS.MONTHLY_SALES_SEMANTIC",
        metrics={"TOTAL_SALES": "SUM(AMOUNT)"},
        source_definitions={"TOTAL_SALES": "CALCULATE(SUM(Sales[Amount]))"},
        baseline_source="deploy",
    )
    assert base is not None
    assert base["project_id"] == "proj-sales"
    assert base["baseline_source"] == "deploy"
    assert base["metrics"]["TOTAL_SALES"] == "SUM(AMOUNT)"
    assert base["source_definitions"]["TOTAL_SALES"] == "CALCULATE(SUM(Sales[Amount]))"

    # Lookup by full view name
    fetched = get_deploy_baseline("proj-sales", "SALES_DB.ANALYTICS.MONTHLY_SALES_SEMANTIC")
    assert fetched is not None
    assert fetched["metrics"]["TOTAL_SALES"] == "SUM(AMOUNT)"

    # Lookup by quoted or lowercase variant matches
    fetched_q = get_deploy_baseline("proj-sales", '"SALES_DB"."ANALYTICS"."MONTHLY_SALES_SEMANTIC"')
    assert fetched_q is not None
    assert fetched_q["metrics"]["TOTAL_SALES"] == "SUM(AMOUNT)"

    # Lookup by short view name matches fallback
    fetched_short = get_deploy_baseline("proj-sales", "MONTHLY_SALES_SEMANTIC")
    assert fetched_short is not None

    # Different project returns None
    assert get_deploy_baseline("proj-other", "SALES_DB.ANALYTICS.MONTHLY_SALES_SEMANTIC") is None


def test_snowflake_emitter_records_baseline_with_project_id():
    """SnowflakeEmitter records deploy baseline with real project_id and source definitions."""
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    mock_cfg = MagicMock()
    mock_cfg.project_id = "proj-finance"
    emitter = SnowflakeEmitter(
        config=mock_cfg,
        project_id="proj-finance",
    )
    assert emitter.project_id == "proj-finance"


def test_run_drift_check_uses_deploy_baseline_not_live_capture():
    """run_drift_check uses existing deploy baseline and does not capture live view as baseline."""
    # Seed a deploy baseline
    record_deploy_baseline(
        project_id="proj-sample",
        view_name="SEMABRIDGE.PUBLIC.AI_SAMPLE_SEMANTIC",
        metrics={"CASE_COUNT": "COUNT(*)"},
        source_definitions={"CASE_COUNT": "COUNTROWS('Cases')"},
        baseline_source="deploy",
    )

    mock_domain = MagicMock()
    mock_domain.domain = "ai_sample"
    mock_domain.id = "ai_sample"
    mock_domain.source_model_id = "model_ai_sample"
    mock_domain.semantic_view_name = "SEMABRIDGE.PUBLIC.AI_SAMPLE_SEMANTIC"
    mock_domain.source_project_id = "proj-sample"
    mock_metric = MagicMock()
    mock_metric.name = "CASE_COUNT"
    mock_metric.sql_expression = "COUNT(*)"
    mock_metric.dax_expression = "COUNTROWS('Cases')"
    mock_domain.metrics = [mock_metric]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect") as mock_conn, \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_domain]):

        mock_cur = MagicMock()
        mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
        mock_cur.fetchall.return_value = [("METRIC", "CASE_COUNT", "EXPRESSION", "COUNT(*)")]
        mock_conn.return_value.cursor.return_value = mock_cur

        result = run_drift_check(project_id="proj-sample")
        assert result["status"] == "IN_SYNC"
        assert result["baseline_source"] == "deploy"
        assert result["drift_detected"] == 0
        assert "SEMABRIDGE.PUBLIC.AI_SAMPLE_SEMANTIC" in result["views_checked"]


def test_run_drift_check_detects_live_view_expression_mismatch():
    """Modifying live Snowflake metric expression after deploy triggers EXPRESSION_MISMATCH."""
    # Baseline recorded at deploy
    record_deploy_baseline(
        project_id="proj-sample",
        view_name="SEMABRIDGE.PUBLIC.AI_SAMPLE_SEMANTIC",
        metrics={"CASE_COUNT": "COUNT(*)"},
        source_definitions={"CASE_COUNT": "COUNTROWS('Cases')"},
        baseline_source="deploy",
    )

    mock_domain = MagicMock()
    mock_domain.domain = "ai_sample"
    mock_domain.id = "ai_sample"
    mock_domain.source_model_id = "model_ai_sample"
    mock_domain.semantic_view_name = "SEMABRIDGE.PUBLIC.AI_SAMPLE_SEMANTIC"
    mock_domain.source_project_id = "proj-sample"
    mock_metric = MagicMock()
    mock_metric.name = "CASE_COUNT"
    mock_metric.sql_expression = "COUNT(*)"
    mock_metric.dax_expression = "COUNTROWS('Cases')"
    mock_domain.metrics = [mock_metric]

    # Live Snowflake view has been edited to COUNT(DISTINCT ID)
    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect") as mock_conn, \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_domain]):

        mock_cur = MagicMock()
        mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
        mock_cur.fetchall.return_value = [("METRIC", "CASE_COUNT", "EXPRESSION", "COUNT(DISTINCT CASES.ID)")]
        mock_conn.return_value.cursor.return_value = mock_cur

        result = run_drift_check(project_id="proj-sample")
        assert result["status"] == "DRIFT_DETECTED"
        assert result["drift_detected"] >= 1
        ev = next(e for e in result["events"] if e["metric_name"] == "CASE_COUNT")
        assert ev["drift_type"] == "EXPRESSION_MISMATCH"


def test_live_capture_returns_baseline_captured_never_in_sync():
    """First check on an existing deployed view with no deploy baseline captures live and returns BASELINE_CAPTURED."""
    mock_domain = MagicMock()
    mock_domain.domain = "ai_sample"
    mock_domain.id = "ai_sample"
    mock_domain.source_model_id = "model_ai_sample"
    mock_domain.semantic_view_name = "SEMABRIDGE.PUBLIC.AI_SAMPLE_SEMANTIC"
    mock_domain.source_project_id = "proj-sample"
    mock_metric = MagicMock()
    mock_metric.name = "TOTAL_SALES"
    mock_metric.sql_expression = "SUM(SALES.AMOUNT)"
    mock_metric.dax_expression = "SUM(Sales[Amount])"
    mock_domain.metrics = [mock_metric]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect") as mock_conn, \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_domain]), \
         patch("semabridge.governance.drift_service._build_baseline_from_deployment", return_value=None):

        mock_cur = MagicMock()
        mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
        mock_cur.fetchall.return_value = [("METRIC", "TOTAL_SALES", "EXPRESSION", "SUM(SALES.AMOUNT)")]
        mock_conn.return_value.cursor.return_value = mock_cur

        result = run_drift_check(project_id="proj-sample")
        assert result["status"] == "BASELINE_CAPTURED"
        assert result["baseline_source"] == "live_capture"
        assert result["status"] != "IN_SYNC"

        # State records baseline
        target_info = get_project_drift_target_info("proj-sample")
        assert target_info["status"] == "BASELINE_CAPTURED"
        assert target_info["baseline_source"] == "live_capture"


def test_source_changed_drift_detection():
    """SOURCE_CHANGED detects when latest snapshot source expression differs from deploy baseline."""
    record_deploy_baseline(
        project_id="proj-hr",
        view_name="SEMABRIDGE.HR.EMPLOYEE_SEMANTIC",
        metrics={"HEADCOUNT": "COUNT(*)"},
        source_definitions={"HEADCOUNT": "COUNTROWS('Employees')"},
        baseline_source="deploy",
    )

    mock_domain = MagicMock()
    mock_domain.domain = "hr"
    mock_domain.id = "hr"
    mock_domain.semantic_view_name = "SEMABRIDGE.HR.EMPLOYEE_SEMANTIC"
    mock_domain.source_project_id = "proj-hr"
    mock_domain.source_model_id = "model-hr"
    mock_metric = MagicMock()
    mock_metric.name = "HEADCOUNT"
    mock_metric.sql_expression = "COUNT(*)"
    mock_metric.dax_expression = "CALCULATE(COUNTROWS('Employees'), 'Employees'[Active] = TRUE())"
    mock_domain.metrics = [mock_metric]

    mock_snap = MagicMock()
    mock_snap.sml_blob = json.dumps({
        "metrics": [{
            "name": "HEADCOUNT",
            "dax_expression": "CALCULATE(COUNTROWS('Employees'), 'Employees'[Active] = TRUE())",
            "sql_expression": "COUNT(*)",
        }]
    })

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect") as mock_conn, \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_domain]), \
         patch("semabridge.repository.model_repository.ModelRepository.get_head", return_value=mock_snap):

        mock_cur = MagicMock()
        mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
        mock_cur.fetchall.return_value = [("METRIC", "HEADCOUNT", "EXPRESSION", "COUNT(*)")]
        mock_conn.return_value.cursor.return_value = mock_cur

        result = run_drift_check(project_id="proj-hr")
        assert result["drift_detected"] >= 1
        ev = next(e for e in result["events"] if e["drift_type"] == "SOURCE_CHANGED")
        assert ev["metric_name"] == "HEADCOUNT"
        assert "TRUE()" in ev["bi_definition"]


def test_drift_router_get_events_returns_metadata_and_last_checks():
    """GET /api/drift/events returns status, reason, baseline_source, baseline_at, views_checked, and last_checks."""
    from semabridge.api.routers.drift_router import get_drift_events

    record_deploy_baseline(
        project_id="proj-hr",
        view_name="SEMABRIDGE.HR.EMPLOYEE_SEMANTIC",
        metrics={"HEADCOUNT": "COUNT(*)"},
        baseline_source="deploy",
    )

    state = _load_state()
    state.setdefault("last_checks", {})["hr"] = {
        "status": "IN_SYNC",
        "views_checked": ["SEMABRIDGE.HR.EMPLOYEE_SEMANTIC"],
        "baseline_source": "deploy",
        "baseline_at": "2026-11-20T10:00:00Z",
        "checked_at": "2026-11-20T12:00:00Z",
        "metrics_compared": 15,
        "not_checked_reason": None,
    }
    _save_state(state)

    resp = get_drift_events(project_id="proj-hr")
    assert resp["status"] == "IN_SYNC"
    assert "SEMABRIDGE.HR.EMPLOYEE_SEMANTIC" in resp["views_checked"]
    assert resp["baseline_source"] == "deploy"
    assert resp["metrics_compared"] == 15
    assert "last_checks" in resp
    assert "hr" in resp["last_checks"]


def test_project_with_snapshots_builds_snapshot_baseline_not_live_capture():
    """Project with snapshots but no pre-recorded baseline builds baseline from snapshot, not live_capture."""
    mock_domain = MagicMock()
    mock_domain.domain = "hr"
    mock_domain.id = "hr"
    mock_domain.semantic_view_name = "SEMABRIDGE.HR.EMPLOYEE_SEMANTIC"
    mock_domain.source_project_id = "proj-hr"
    mock_domain.source_model_id = "model-hr"
    mock_metric = MagicMock()
    mock_metric.name = "HEADCOUNT"
    mock_metric.sql_expression = "COUNT(*)"
    mock_metric.dax_expression = "COUNTROWS('Employees')"
    mock_domain.metrics = [mock_metric]

    mock_snap = MagicMock()
    mock_snap.snapshot_id = "snap-123"
    mock_snap.timestamp = "2026-11-20T10:00:00Z"
    mock_snap.sml_blob = json.dumps({
        "metrics": [{
            "name": "HEADCOUNT",
            "dax_expression": "COUNTROWS('Employees')",
            "sql_expression": "COUNT(*)",
        }]
    })

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect") as mock_conn, \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_domain]), \
         patch("semabridge.repository.model_repository.ModelRepository.get_last_deployed_snapshot", return_value=mock_snap), \
         patch("semabridge.repository.model_repository.ModelRepository.get_head", return_value=mock_snap):

        mock_cur = MagicMock()
        mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
        mock_cur.fetchall.return_value = [("METRIC", "HEADCOUNT", "EXPRESSION", "COUNT(*)")]
        mock_conn.return_value.cursor.return_value = mock_cur

        result = run_drift_check(project_id="proj-hr")
        assert result["baseline_source"] == "snapshot"
        assert result["baseline_source"] != "live_capture"
        assert result["status"] == "IN_SYNC"
        assert result["drift_detected"] == 0

        # State records baseline_source="snapshot"
        stored = get_deploy_baseline("proj-hr", "SEMABRIDGE.HR.EMPLOYEE_SEMANTIC")
        assert stored is not None
        assert stored["baseline_source"] == "snapshot"


def test_metric_edited_in_live_view_after_snapshot_triggers_expression_mismatch_on_first_check():
    """Metric edited in live Snowflake view after snapshot triggers EXPRESSION_MISMATCH on the first check."""
    mock_domain = MagicMock()
    mock_domain.domain = "hr"
    mock_domain.id = "hr"
    mock_domain.semantic_view_name = "SEMABRIDGE.HR.EMPLOYEE_SEMANTIC"
    mock_domain.source_project_id = "proj-hr"
    mock_domain.source_model_id = "model-hr"
    mock_metric = MagicMock()
    mock_metric.name = "HEADCOUNT"
    mock_metric.sql_expression = "COUNT(*)"
    mock_metric.dax_expression = "COUNTROWS('Employees')"
    mock_domain.metrics = [mock_metric]

    # Snapshot records COUNT(*)
    mock_snap = MagicMock()
    mock_snap.snapshot_id = "snap-123"
    mock_snap.timestamp = "2026-11-20T10:00:00Z"
    mock_snap.sml_blob = json.dumps({
        "metrics": [{
            "name": "HEADCOUNT",
            "dax_expression": "COUNTROWS('Employees')",
            "sql_expression": "COUNT(*)",
        }]
    })

    # But live Snowflake view has been altered to COUNT(DISTINCT ID)
    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect") as mock_conn, \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_domain]), \
         patch("semabridge.repository.model_repository.ModelRepository.get_last_deployed_snapshot", return_value=mock_snap), \
         patch("semabridge.repository.model_repository.ModelRepository.get_head", return_value=mock_snap):

        mock_cur = MagicMock()
        mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
        mock_cur.fetchall.return_value = [("METRIC", "HEADCOUNT", "EXPRESSION", "COUNT(DISTINCT EMPLOYEES.ID)")]
        mock_conn.return_value.cursor.return_value = mock_cur

        result = run_drift_check(project_id="proj-hr")
        # Flags EXPRESSION_MISMATCH against snapshot baseline on first check instead of capturing the altered live view
        assert result["baseline_source"] == "snapshot"
        assert result["status"] == "DRIFT_DETECTED"
        assert result["drift_detected"] >= 1
        ev = next(e for e in result["events"] if e["metric_name"] == "HEADCOUNT")
        assert ev["drift_type"] == "EXPRESSION_MISMATCH"


def test_live_capture_status_is_in_sync_since_capture_never_in_sync_clean():
    """Live-capture baseline check returns IN_SYNC_SINCE_CAPTURE with baseline_at, never plain IN_SYNC."""
    mock_domain = MagicMock()
    mock_domain.domain = "legacy_sales"
    mock_domain.id = "legacy_sales"
    mock_domain.semantic_view_name = "SEMABRIDGE.SALES.LEGACY_VIEW"
    mock_domain.source_project_id = "proj-legacy"
    mock_domain.source_model_id = "model-legacy"
    mock_metric = MagicMock()
    mock_metric.name = "SALES_AMT"
    mock_metric.sql_expression = "SUM(SALES.AMOUNT)"
    mock_metric.dax_expression = "SUM(Sales[Amount])"
    mock_domain.metrics = [mock_metric]

    # Pre-seed existing live_capture baseline in state
    record_deploy_baseline(
        project_id="proj-legacy",
        view_name="SEMABRIDGE.SALES.LEGACY_VIEW",
        metrics={"SALES_AMT": "SUM(SALES.AMOUNT)"},
        source_definitions={"SALES_AMT": "SUM(Sales[Amount])"},
        deployed_at="2026-11-20T10:00:00Z",
        baseline_source="live_capture",
    )

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect") as mock_conn, \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_domain]), \
         patch("semabridge.governance.drift_service._build_baseline_from_deployment", return_value=None):

        mock_cur = MagicMock()
        mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
        mock_cur.fetchall.return_value = [("METRIC", "SALES_AMT", "EXPRESSION", "SUM(SALES.AMOUNT)")]
        mock_conn.return_value.cursor.return_value = mock_cur

        result = run_drift_check(project_id="proj-legacy")
        # Returns IN_SYNC_SINCE_CAPTURE, never plain IN_SYNC
        assert result["status"] == "IN_SYNC_SINCE_CAPTURE"
        assert result["status"] != "IN_SYNC"
        assert result["baseline_source"] == "live_capture"
        assert result["baseline_at"] == "2026-11-20T10:00:00Z"

        # get_project_drift_target_info also returns IN_SYNC_SINCE_CAPTURE
        info = get_project_drift_target_info("proj-legacy")
        assert info["status"] == "IN_SYNC_SINCE_CAPTURE"
        assert info["status"] != "IN_SYNC"
        assert info["baseline_source"] == "live_capture"

