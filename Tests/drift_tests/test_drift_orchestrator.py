"""Unit and integration tests for drift check orchestrator (run_drift_check)."""

from unittest.mock import MagicMock, patch
import pytest

from semabridge.governance.drift_service import run_drift_check


def test_orchestrator_missing_baseline():
    """When no deploy baseline exists, status must be NOT_CHECKED with clear redeploy guidance."""
    mock_dom = MagicMock()
    mock_dom.domain = "test_domain"
    mock_dom.semantic_view_name = "DB.SCH.TEST_VIEW"
    mock_dom.metrics = [MagicMock(name="METRIC_A")]

    with patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]), \
         patch("semabridge.ai.project_connection._project_meta", return_value={"platform": "SNOWFLAKE"}), \
         patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "test", "user": "test"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("semabridge.governance.drift_service.get_deploy_baseline", return_value=None), \
         patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value="SELECT 1"):
        result = run_drift_check(project_id="test_proj", view="DB.SCH.TEST_VIEW")
        assert result["status"] == "NOT_CHECKED"
        assert "no deploy baseline" in str(result.get("not_checked_reason", "")).lower() or "no_deploy_baseline" in str(result.get("not_checked_reason", "")).lower()


def test_orchestrator_unsupported_platform():
    """An unsupported warehouse platform returns NOT_CHECKED with clear platform message."""
    mock_dom = MagicMock()
    mock_dom.domain = "test_domain"
    mock_dom.semantic_view_name = "DB.SCH.TEST_VIEW"

    with patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):
        with patch("semabridge.ai.project_connection._project_meta", return_value={"platform": "UNSUPPORTED_WH"}):
            result = run_drift_check(project_id="test_proj_unsupported", view="DB.SCH.TEST_VIEW")
            assert result["status"] == "NOT_CHECKED"
            assert "not supported" in str(result.get("reason", "")).lower()


def test_orchestrator_concurrency_lock():
    """When a check is already running for the same view, subsequent check returns CHECK_IN_PROGRESS."""
    from semabridge.governance.drift_service import _ACTIVE_CHECKS, _ACTIVE_CHECKS_LOCK, _full_view_key, _pkey

    pid = "test_proj_concurrent"
    view = "DB.SCH.CONCURRENT_VIEW"
    lock_key = f"{_pkey(pid)}::{_full_view_key(view)}"

    with _ACTIVE_CHECKS_LOCK:
        _ACTIVE_CHECKS.add(lock_key)

    try:
        result = run_drift_check(project_id=pid, view=view)
        assert result["status"] == "CHECK_IN_PROGRESS"
        assert result["not_checked_reason"] == "CHECK_IN_PROGRESS"
    finally:
        with _ACTIVE_CHECKS_LOCK:
            _ACTIVE_CHECKS.discard(lock_key)


def test_orchestrator_mock_mode_e2e(tmp_path):
    """End-to-end drift check in mock mode using RecordedFixtureAdapter."""
    live_sql = """
    CREATE OR REPLACE SEMANTIC VIEW DB.SCH.FACT_VIEW
    TABLES (
        T AS DB.SCH.T
    )
    FACTS (
        T.AMOUNT AS AMOUNT
    )
    METRICS (
        T.SUM(AMOUNT) AS TOTAL_ITEMS COMMENT = 'Live comment',
        T.COUNT(AMOUNT) AS ITEM_COUNT
    );
    """
    fixture_file = tmp_path / "fact_view.sql"
    fixture_file.write_text(live_sql, encoding="utf-8")

    baseline_sql = """
    CREATE OR REPLACE SEMANTIC VIEW DB.SCH.FACT_VIEW
    TABLES (
        T AS DB.SCH.T
    )
    FACTS (
        T.AMOUNT AS AMOUNT
    )
    METRICS (
        T.AVG(AMOUNT) AS TOTAL_ITEMS COMMENT = 'Original comment'
    );
    """

    mock_dom = MagicMock()
    mock_dom.domain = "test_domain"
    mock_dom.id = "dom-1"
    mock_dom.semantic_view_name = "DB.SCH.FACT_VIEW"
    mock_dom.source_project_id = "test_proj_mock"
    mock_dom.source_model_id = "model_1"
    mock_dom.metrics = [MagicMock(name="TOTAL_ITEMS")]

    baseline_payload = {
        "platform": "SNOWFLAKE",
        "definition_text": baseline_sql,
        "view_name": "DB.SCH.FACT_VIEW",
        "baseline_source": "deploy",
        "deployed_at": "2026-11-23T12:00:00Z",
    }

    with patch.dict("os.environ", {"SEMABRIDGE_DRIFT_FIXTURE_DIR": str(tmp_path)}):
        with patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):
            with patch("semabridge.ai.project_connection._project_meta", return_value={"platform": "SNOWFLAKE"}):
                with patch("semabridge.governance.drift_service.get_deploy_baseline", return_value=baseline_payload):
                    result = run_drift_check(
                        project_id="test_proj_mock",
                        view="DB.SCH.FACT_VIEW",
                        mode="mock",
                    )
                    assert result["status"] == "DRIFT_DETECTED"
                    assert result["mode"] == "mock"
                    assert result["platform"] == "SNOWFLAKE"
                    assert len(result["events"]) >= 1

                    categories = {e.get("category") for e in result["events"]}
                    assert "EXPRESSION_DRIFT" in categories
                    assert "ADDITION" in categories
                    assert any(e.get("mode") == "mock" for e in result["events"])
