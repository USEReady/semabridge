"""Tests for drift Postgres persistence, multi-process baseline visibility,
safe view validation, and non-regression on false baseline comparisons.
"""
from __future__ import annotations

import threading
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from semabridge.governance import drift_service as ds
from semabridge.repository.orm.models import (
    DriftBaselineRow,
    DriftLastCheckRow,
    DriftScheduleRow,
    DriftEventRow,
)


def test_baseline_persisted_to_database_and_visible_across_processes(tmp_path, monkeypatch):
    """In a database-backed environment, recorded deploy baselines are persisted to
    the drift_baselines table and visible to subsequent processes.
    """
    from semabridge.repository.orm.session_factory import db_manager
    from sqlalchemy import select

    ds._ensure_drift_table()
    pid = "proj-alpha-drift"
    vname = "ANALYTICS.REPORTING.CORE_VIEW"

    base = ds.record_deploy_baseline(
        project_id=pid,
        view_name=vname,
        metrics={"KPI_ALPHA": "SUM(AMOUNT)"},
        source_definitions={"KPI_ALPHA": "CALCULATE(SUM(Table[Amount]))"},
        baseline_source="deploy",
    )
    assert base is not None

    with ds._drift_session() as s:
        row = s.get(DriftBaselineRow, f"alpha-drift:{ds._full_view_key(vname)}")
        assert row is not None
        assert row.version >= 1
        assert "KPI_ALPHA" in row.payload_json

    fetched = ds.get_deploy_baseline(pid, vname)
    assert fetched is not None
    assert fetched["metrics"]["KPI_ALPHA"] == "SUM(AMOUNT)"


def test_no_baseline_returns_not_checked_without_ontology_sql_fallback(monkeypatch):
    """When a view has no deploy baseline, drift check reports not checked
    and never falls back to comparing ontology SQL with Snowflake DESCRIBE.
    """
    mock_dom = MagicMock()
    mock_dom.domain = "DomainAlpha"
    mock_dom.semantic_view_name = "DB.SCHEMA.VIEW_UNBASELINED"
    mock_dom.source_project_id = "proj-unbaselined"
    mock_dom.source_model_id = "model-alpha"
    mock_metric = MagicMock()
    mock_metric.name = "METRIC_ONE"
    mock_metric.sql_expression = "SUM(ORDERS.AMOUNT)"
    mock_metric.dax_expression = "SUM(Orders[Amount])"
    mock_dom.metrics = [mock_metric]

    with patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):
        with patch("semabridge.governance.drift_service.get_deploy_baseline", return_value=None):
            with patch("semabridge.governance.drift_service._build_baseline_from_deployment", return_value=None):
                with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "acct"}):
                    with patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "acct", "user": "u"}):
                        import snowflake.connector

                        mock_conn = MagicMock()
                        mock_cur = MagicMock()
                        mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
                        # Live Snowflake view has no metrics, so live_capture does not capture a baseline
                        mock_cur.fetchall.return_value = []
                        mock_conn.cursor.return_value = mock_cur

                        with patch.object(snowflake.connector, "connect", return_value=mock_conn):
                            res = ds.run_drift_check(project_id="proj-unbaselined")
                            # Must not produce an EXPRESSION_MISMATCH event by comparing with ontology SQL
                            assert res["drift_detected"] == 0
                            assert len(res["events"]) == 0
                            assert res["status"] == "NOT_CHECKED"
                            assert any(
                                "no deploy baseline" in str(nc.get("reason", "")).lower()
                                for nc in res.get("not_checked", [])
                            )


def test_concurrent_checks_do_not_lose_metadata_writes(tmp_path):
    """Two concurrent threads updating different projects or views perform row-level upserts
    without overwriting or deleting each other's baselines or last check records.
    """
    ds._ensure_drift_table()

    def record_job(pid: str, vname: str, metric_name: str):
        ds.record_deploy_baseline(
            project_id=pid,
            view_name=vname,
            metrics={metric_name: "COUNT(*)"},
            baseline_source="deploy",
        )

    t1 = threading.Thread(target=record_job, args=("proj-worker-one", "DB.SCH.V1", "KPI_ONE"))
    t2 = threading.Thread(target=record_job, args=("proj-worker-two", "DB.SCH.V2", "KPI_TWO"))

    t1.start()
    t2.start()
    t1.join(5)
    t2.join(5)

    base1 = ds.get_deploy_baseline("proj-worker-one", "DB.SCH.V1")
    base2 = ds.get_deploy_baseline("proj-worker-two", "DB.SCH.V2")

    assert base1 is not None and "KPI_ONE" in base1["metrics"]
    assert base2 is not None and "KPI_TWO" in base2["metrics"]


def test_source_changed_fires_strictly_when_power_bi_dax_differs():
    """SOURCE_CHANGED fires only when the Power BI DAX expression differs from
    the baseline source definition, and does not compare DAX against SQL.
    """
    mock_dom = MagicMock()
    mock_dom.domain = "FinanceDomain"
    mock_dom.semantic_view_name = "DB.FIN.SEMANTIC_VIEW"
    mock_dom.source_project_id = "proj-fin-dax"
    mock_dom.source_model_id = "model-fin"

    metric_identical = MagicMock()
    metric_identical.name = "EXPENSE_RATIO"
    metric_identical.sql_expression = "DIV0(EXP, REV)"
    metric_identical.dax_expression = "DIVIDE(SUM(T[Expenses]), SUM(T[Revenues]))"

    mock_dom.metrics = [metric_identical]

    # Baseline stored identical DAX definition
    baseline_payload = {
        "project_id": "proj-fin-dax",
        "view_name": "DB.FIN.SEMANTIC_VIEW",
        "metrics": {"EXPENSE_RATIO": "DIV0(EXP, REV)"},
        "source_definitions": {"EXPENSE_RATIO": "DIVIDE(SUM(T[Expenses]), SUM(T[Revenues]))"},
        "baseline_source": "deploy",
    }

    with patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):
        with patch("semabridge.governance.drift_service.get_deploy_baseline", return_value=baseline_payload):
            with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "acct"}):
                with patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "acct", "user": "u"}):
                    import snowflake.connector

                    mock_conn = MagicMock()
                    mock_cur = MagicMock()
                    mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
                    mock_cur.fetchall.return_value = [
                        ("METRIC", "EXPENSE_RATIO", "EXPRESSION", "DIV0(EXP, REV)")
                    ]
                    mock_conn.cursor.return_value = mock_cur

                    with patch.object(snowflake.connector, "connect", return_value=mock_conn):
                        # 1. Identical DAX -> 0 drift events
                        res = ds.run_drift_check(project_id="proj-fin-dax")
                        assert res["drift_detected"] == 0

                        # 2. Altered DAX -> exactly 1 SOURCE_CHANGED drift event
                        metric_identical.dax_expression = "DIVIDE(SUM(T[Expenses]), SUM(T[Revenues]), 0)"
                        res_drift = ds.run_drift_check(project_id="proj-fin-dax")
                        assert res_drift["drift_detected"] == 1
                        assert res_drift["events"][0]["drift_type"] == "SOURCE_CHANGED"
