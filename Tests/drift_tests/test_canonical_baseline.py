"""Unit and lifecycle tests for Canonical Baseline Store.

All tests are placed in Tests/drift_tests as required.
Tests verify:
1. Baseline persistence and retrieval
2. Version incrementing and history tracking
3. Preserving baselines when drift events are cleared
4. Accurate tracking of not_deployed_objects from remediation
5. Support for descriptions alongside expressions
6. Refusal of fallback to unverified local files
7. Backward compatibility for legacy callers of record_deploy_baseline
"""

from __future__ import annotations

import pytest
from semabridge.governance.drift import (
    get_active_baseline,
    is_object_not_deployed,
    list_baseline_history,
    save_deploy_baseline,
)
from semabridge.governance.drift_service import (
    clear_drift_events,
    record_deploy_baseline,
)


@pytest.fixture(autouse=True)
def clean_test_drift_state():
    """Ensure clean state before each test run."""
    clear_drift_events(_allow_all_for_testing=True)
    yield


def test_save_and_retrieve_active_baseline():
    """Verify that a deploy baseline persists properly with all metadata."""
    project_id = "test-project-alpha"
    platform = "SNOWFLAKE"
    view_name = "DB_CORE.ANALYTICS.SALES_SUMMARY_VIEW"
    ddl_text = "CREATE OR REPLACE SEMANTIC VIEW DB_CORE.ANALYTICS.SALES_SUMMARY_VIEW ..."
    metrics = {
        "TOTAL_REVENUE": "SUM(amount)",
        "ACTIVE_COUNT": "COUNT(DISTINCT account_id)",
    }
    descriptions = {
        "TOTAL_REVENUE": "Sum of all recognized transaction revenues",
        "ACTIVE_COUNT": "Unique count of active client accounts",
    }
    not_deployed = ["LEGACY_MARGIN_PCT"]

    saved = save_deploy_baseline(
        project_id=project_id,
        platform=platform,
        view_name=view_name,
        definition_text=ddl_text,
        not_deployed_objects=not_deployed,
        snapshot_id="snap-001",
        deployed_at="2026-11-23T10:00:00Z",
        metrics=metrics,
        descriptions=descriptions,
    )

    assert saved is not None
    assert saved["version"] == 1
    assert saved["platform"] == "SNOWFLAKE"
    assert saved["view_name"] == view_name
    assert saved["definition_text"] == ddl_text
    assert saved["not_deployed_objects"] == ["LEGACY_MARGIN_PCT"]
    assert saved["metrics"]["TOTAL_REVENUE"] == "SUM(amount)"
    assert saved["descriptions"]["TOTAL_REVENUE"] == "Sum of all recognized transaction revenues"

    # Retrieve active baseline
    retrieved = get_active_baseline(project_id, view_name)
    assert retrieved is not None
    assert retrieved["version"] == 1
    assert retrieved["platform"] == "SNOWFLAKE"
    assert retrieved["metrics"]["TOTAL_REVENUE"] == "SUM(amount)"
    assert retrieved["descriptions"]["ACTIVE_COUNT"] == "Unique count of active client accounts"


def test_baseline_versioning_and_history():
    """Subsequent deploys must increment version and preserve history without losing data."""
    project_id = "test-project-beta"
    platform = "SNOWFLAKE"
    view_name = "ANALYTICS.METRIC_VIEW"

    # Deploy 1
    saved_v1 = save_deploy_baseline(
        project_id=project_id,
        platform=platform,
        view_name=view_name,
        definition_text="CREATE SEMANTIC VIEW V1...",
        snapshot_id="snap-v1",
        deployed_at="2026-11-23T11:00:00Z",
        metrics={"KPI_A": "SUM(col_a)"},
    )
    assert saved_v1["version"] == 1

    # Deploy 2 (new version)
    saved_v2 = save_deploy_baseline(
        project_id=project_id,
        platform=platform,
        view_name=view_name,
        definition_text="CREATE SEMANTIC VIEW V2...",
        snapshot_id="snap-v2",
        deployed_at="2026-11-23T12:00:00Z",
        metrics={"KPI_A": "SUM(col_a)", "KPI_B": "AVG(col_b)"},
        not_deployed_objects=["KPI_OLD"],
    )
    assert saved_v2["version"] == 2
    assert len(saved_v2["history"]) == 1
    assert saved_v2["history"][0]["version"] == 1
    assert saved_v2["history"][0]["snapshot_id"] == "snap-v1"

    # Active baseline must be version 2
    active = get_active_baseline(project_id, view_name)
    assert active["version"] == 2
    assert active["definition_text"] == "CREATE SEMANTIC VIEW V2..."

    # History listing must show both versions
    history = list_baseline_history(project_id, view_name)
    assert len(history) == 2
    assert history[0]["version"] == 1
    assert history[1]["version"] == 2
    assert history[1]["is_active"] is True


def test_clear_drift_events_preserves_baseline():
    """Clearing drift history must delete events only, NEVER the deploy baselines."""
    project_id = "test-project-gamma"
    view_name = "ANALYTICS.FINANCE_VIEW"

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text="CREATE SEMANTIC VIEW ...",
        metrics={"NET_MARGIN": "AVG(margin)"},
    )

    # Verify baseline exists
    assert get_active_baseline(project_id, view_name) is not None

    # Clear drift events for this project
    cleared = clear_drift_events(project_id=project_id)
    assert isinstance(cleared, int)

    # Baseline must STILL exist!
    active_after = get_active_baseline(project_id, view_name)
    assert active_after is not None
    assert active_after["metrics"]["NET_MARGIN"] == "AVG(margin)"


def test_not_deployed_objects_helper():
    """Verify that is_object_not_deployed correctly identifies remediated/dropped objects."""
    baseline = {
        "not_deployed_objects": ["DISCARDED_CALC", "NULL_EXPRESSION_METRIC"],
    }

    assert is_object_not_deployed(baseline, "DISCARDED_CALC") is True
    # Case-insensitive
    assert is_object_not_deployed(baseline, "discarded_calc") is True
    assert is_object_not_deployed(baseline, "null_expression_metric") is True
    assert is_object_not_deployed(baseline, "VALID_METRIC") is False
    assert is_object_not_deployed(None, "DISCARDED_CALC") is False
    assert is_object_not_deployed(baseline, "") is False


def test_refuse_local_file_fallback():
    """Baseline retrieval must return None when no baseline exists, never guess from disk."""
    non_existent = get_active_baseline("unknown-project-xyz", "NON_EXISTENT_VIEW")
    assert non_existent is None


def test_backward_compatibility_record_deploy_baseline():
    """Legacy callers using drift_service.record_deploy_baseline must continue to function."""
    project_id = "test-project-delta"
    view_name = "CORE.REPORTING_VIEW"

    res = record_deploy_baseline(
        project_id=project_id,
        view_name=view_name,
        metrics={"ORDERS_COUNT": "COUNT(order_id)"},
        deployed_at="2026-11-23T14:00:00Z",
        snapshot_id="snap-legacy",
    )

    assert res is not None
    assert res["version"] >= 1
    assert res["metrics"]["ORDERS_COUNT"] == "COUNT(order_id)"

    # Can be retrieved via new baseline_store API
    retrieved = get_active_baseline(project_id, view_name)
    assert retrieved is not None
    assert retrieved["metrics"]["ORDERS_COUNT"] == "COUNT(order_id)"


def test_emitter_deploy_hook_saves_final_remediated_ddl_and_suppresses_deletion():
    """Verify that Snowflake emitter hook saves the final executed DDL after auto-remediation,
    records dropped metrics in not_deployed_objects, and comparison engine suppresses false deletion.
    """
    from semabridge.governance.drift.adapters.snowflake import SnowflakeAdapter
    from semabridge.governance.drift.comparison import compare_views

    project_id = "proj-remediation-test"
    view_name = "DB.SCHEMA.SALES_REMEDIATED_SEMANTIC"

    # Initial model had two metrics: VALID_REVENUE and BROKEN_COMPLEX_KPI
    # During deploy, BROKEN_COMPLEX_KPI was rejected by Snowflake and dropped during auto-remediation
    final_executed_ddl = """
    CREATE OR REPLACE SEMANTIC VIEW DB.SCHEMA.SALES_REMEDIATED_SEMANTIC
    TABLES ( orders AS DB.SCHEMA.ORDERS )
    METRICS (
        orders.VALID_REVENUE AS SUM(amount) COMMENT 'Total sales amount'
    );
    """
    dropped_metrics_from_emitter = [
        {"metric": "BROKEN_COMPLEX_KPI", "reason": "unsupported window function", "by_design": False}
    ]

    # Simulate emitter calling baseline_store.save_deploy_baseline in Step 6c
    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text=final_executed_ddl,
        not_deployed_objects=[d["metric"] for d in dropped_metrics_from_emitter],
        metrics={"VALID_REVENUE": "SUM(amount)"},
    )

    # 1. Verify baseline in store contains final DDL and dropped objects
    baseline = get_active_baseline(project_id, view_name)
    assert baseline is not None
    assert "BROKEN_COMPLEX_KPI" in baseline["not_deployed_objects"]
    assert "VALID_REVENUE" in baseline["definition_text"]
    assert "BROKEN_COMPLEX_KPI" not in baseline["definition_text"]

    # 2. Live warehouse contains only VALID_REVENUE
    adapter = SnowflakeAdapter()
    base_view_def = adapter.parse_definition(
        raw_text=baseline["definition_text"],
        view_name=view_name,
        source="baseline",
    )
    base_view_def.not_deployed_objects = baseline["not_deployed_objects"]

    live_view_def = adapter.parse_definition(
        raw_text=final_executed_ddl,
        view_name=view_name,
        source="live",
    )

    # 3. Compare views: must produce 0 findings (no false deletion for BROKEN_COMPLEX_KPI)
    findings = compare_views(baseline=base_view_def, live=live_view_def)
    assert len(findings) == 0, f"Expected 0 drift findings after remediation, got {findings}"


def test_baseline_full_name_key_two_schemas_same_short_name_separate():
    """Two views with the same short name in different schemas/catalogs remain strictly separate."""
    project_id = "test-proj-namespacing"
    view_east = "DB.SCHEMA_EAST.SALES_VIEW"
    view_west = "DB.SCHEMA_WEST.SALES_VIEW"

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_east,
        definition_text="CREATE VIEW EAST ...",
        metrics={"EAST_METRIC": "SUM(east_val)"},
    )

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_west,
        definition_text="CREATE VIEW WEST ...",
        metrics={"WEST_METRIC": "SUM(west_val)"},
    )

    base_east = get_active_baseline(project_id, view_east)
    base_west = get_active_baseline(project_id, view_west)

    assert base_east is not None
    assert base_west is not None
    assert "EAST_METRIC" in base_east["metrics"]
    assert "WEST_METRIC" not in base_east["metrics"]
    assert "WEST_METRIC" in base_west["metrics"]
    assert "EAST_METRIC" not in base_west["metrics"]


def test_baseline_history_size_from_configuration(monkeypatch):
    """History retention limit honors SEMABRIDGE_DRIFT_MAX_BASELINE_HISTORY configuration."""
    monkeypatch.setenv("SEMABRIDGE_DRIFT_MAX_BASELINE_HISTORY", "3")
    project_id = "test-proj-history-limit"
    view_name = "DB.SCH.HISTORY_VIEW"

    # Deploy 6 times
    for v in range(1, 7):
        save_deploy_baseline(
            project_id=project_id,
            platform="SNOWFLAKE",
            view_name=view_name,
            definition_text=f"CREATE VIEW V{v} ...",
            snapshot_id=f"snap-{v}",
            metrics={"M": f"SUM(c{v})"},
        )

    history = list_baseline_history(project_id, view_name)
    # 3 historical versions + 1 active version = 4 entries
    assert len(history) <= 4


def test_multi_warehouse_baseline_isolation_snowflake_and_databricks(clean_test_drift_state):
    """Verify that multiple platforms under the same project/view never overwrite or compete."""
    project_id = "test-proj-dual-target"
    view_name = "CORP.FINANCE.REVENUE_VIEW"

    # Deploy to Snowflake
    snow_base = save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text="CREATE SEMANTIC VIEW CORP.FINANCE.REVENUE_VIEW ...",
        metrics={"TOTAL_REV": "SUM(AMOUNT)"},
    )
    assert snow_base["platform"] == "SNOWFLAKE"

    # Deploy same view name to Databricks
    dbx_base = save_deploy_baseline(
        project_id=project_id,
        platform="DATABRICKS",
        view_name=view_name,
        definition_text="version: 1.1\nmodels:\n  - name: revenue_view\n    measures:\n      - name: total_rev",
        metrics={"TOTAL_REV": "sum(amount)"},
    )
    assert dbx_base["platform"] == "DATABRICKS"

    # Retrieve platform-specific baselines
    retrieved_snow = get_active_baseline(project_id, view_name, platform="SNOWFLAKE")
    retrieved_dbx = get_active_baseline(project_id, view_name, platform="DATABRICKS")

    assert retrieved_snow is not None
    assert retrieved_snow["platform"] == "SNOWFLAKE"
    assert "CREATE SEMANTIC VIEW" in retrieved_snow["definition_text"]

    assert retrieved_dbx is not None
    assert retrieved_dbx["platform"] == "DATABRICKS"
    assert "version: 1.1" in retrieved_dbx["definition_text"]


def test_database_failure_during_save_raises_and_leaves_no_ghost_baseline():
    """A database failure during save raises an exception and leaves no cache entry and no reported baseline."""
    from unittest.mock import patch
    from semabridge.governance.drift.baseline_store import _memory_baselines

    project_id = "test-proj-fail-save"
    view_name = "DB.SCHEMA.FAIL_VIEW"

    with patch("semabridge.governance.drift.baseline_store._get_db_session", side_effect=RuntimeError("DB Connection Lost")):
        with pytest.raises(RuntimeError, match="DB Connection Lost"):
            save_deploy_baseline(
                project_id=project_id,
                platform="SNOWFLAKE",
                view_name=view_name,
                metrics={"KPI": "SUM(x)"},
            )

    # Must leave no cache entry and report no baseline
    assert get_active_baseline(project_id, view_name) is None
    clean_p = "test-proj-fail-save".removeprefix("proj-")
    assert clean_p not in _memory_baselines or "db.schema.fail_view" not in _memory_baselines.get(clean_p, {})


def test_after_restart_with_no_baseline_returns_not_checked_with_no_deploy_baseline():
    """After a service restart when no deploy baseline exists in the database, drift check reports NOT_CHECKED with NO_DEPLOY_BASELINE and redeploy action."""
    from unittest.mock import MagicMock, patch
    from semabridge.governance.drift_service import run_drift_check, get_project_drift_target_info

    project_id = "test-proj-restart-empty"
    view_name = "DB.SCHEMA.UNCONFIGURED_VIEW"

    mock_domain = MagicMock()
    mock_domain.domain = "Domain Empty"
    mock_domain.semantic_view_name = view_name
    mock_domain.source_project_id = project_id
    mock_domain.metrics = [MagicMock(name="KPI", sql_expression="SUM(x)", dax_expression="SUM(x)")]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect"), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_domain]):

        res = run_drift_check(project_id=project_id, view=view_name)
        assert res["status"] == "NOT_CHECKED"
        assert res["reason_code"] == "NO_DEPLOY_BASELINE"
        assert res["not_checked_reason"] == "NO_DEPLOY_BASELINE"
        assert res.get("proposed_action") == "redeploy"

        info = get_project_drift_target_info(project_id, view=view_name)
        assert info["status"] == "NOT_CHECKED"
        assert info["not_checked_reason"] == "NO_DEPLOY_BASELINE"


def test_two_projects_deploying_same_view_name_maintain_separate_baselines():
    """Ensure baseline belongs strictly to the project deployed.
    Two projects deploying the same view name must not share a row or overwrite each other.
    """
    from unittest.mock import MagicMock, patch
    from semabridge.governance.drift_service import run_drift_check

    shared_view = "CORP.FINANCE.SHARED_METRIC_VIEW"
    project_a = "proj-client-a"
    project_b = "proj-client-b"

    ddl_a = """
    CREATE OR REPLACE SEMANTIC VIEW CORP.FINANCE.SHARED_METRIC_VIEW
    TABLES ( sales AS CORP.FINANCE.SALES )
    METRICS (
        sales.REVENUE_METRIC AS SUM(amount) COMMENT 'Revenue for Client A'
    );
    """
    ddl_b = """
    CREATE OR REPLACE SEMANTIC VIEW CORP.FINANCE.SHARED_METRIC_VIEW
    TABLES ( sales AS CORP.FINANCE.SALES )
    METRICS (
        sales.PROFIT_METRIC AS SUM(gain) COMMENT 'Profit for Client B'
    );
    """

    # 1. Project A deploys
    res_a = save_deploy_baseline(
        project_id=project_a,
        platform="SNOWFLAKE",
        view_name=shared_view,
        definition_text=ddl_a,
        metrics={"REVENUE_METRIC": "SUM(amount)"},
    )
    assert res_a["version"] == 1
    assert "REVENUE_METRIC" in (res_a.get("metrics") or {})

    # 2. Project B deploys to the same view name
    res_b = save_deploy_baseline(
        project_id=project_b,
        platform="SNOWFLAKE",
        view_name=shared_view,
        definition_text=ddl_b,
        metrics={"PROFIT_METRIC": "SUM(gain)"},
    )
    assert res_b["version"] == 1
    assert "PROFIT_METRIC" in (res_b.get("metrics") or {})

    # 3. Retrieve baseline for Project A: must strictly be A's baseline, version 1
    base_a = get_active_baseline(project_a, shared_view, platform="SNOWFLAKE")
    assert base_a is not None
    assert base_a["version"] == 1
    assert "REVENUE_METRIC" in (base_a.get("metrics") or {})
    assert "PROFIT_METRIC" not in (base_a.get("metrics") or {})

    # 4. Retrieve baseline for Project B: must strictly be B's baseline, version 1
    base_b = get_active_baseline(project_b, shared_view, platform="SNOWFLAKE")
    assert base_b is not None
    assert base_b["version"] == 1
    assert "PROFIT_METRIC" in (base_b.get("metrics") or {})
    assert "REVENUE_METRIC" not in (base_b.get("metrics") or {})

    # 5. Drift check for each project finds its own baseline and reports 0 findings
    dom_a = MagicMock()
    dom_a.domain = "Finance A"
    dom_a.semantic_view_name = shared_view
    dom_a.source_project_id = project_a
    dom_a.metrics = [MagicMock(name="REVENUE_METRIC", sql_expression="SUM(amount)", dax_expression="SUM(amount)")]

    dom_b = MagicMock()
    dom_b.domain = "Finance B"
    dom_b.semantic_view_name = shared_view
    dom_b.source_project_id = project_b
    dom_b.metrics = [MagicMock(name="PROFIT_METRIC", sql_expression="SUM(gain)", dax_expression="SUM(gain)")]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect"), \
         patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=ddl_a), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom_a]):

        check_a = run_drift_check(project_id=project_a, view=shared_view)
        assert check_a["status"] in ("IN_SYNC", "COMPLETED")
        assert check_a["drift_detected"] == 0

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect"), \
         patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=ddl_b), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom_b]):

        check_b = run_drift_check(project_id=project_b, view=shared_view)
        assert check_b["status"] in ("IN_SYNC", "COMPLETED")
        assert check_b["drift_detected"] == 0





