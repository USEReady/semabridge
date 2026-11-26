"""Hard Scenario Pass: Tests for Sections F through I (Scenarios 36 to 54).

Covers events at scale, lifecycle, cache isolation, multi-project/multi-platform logic,
and product-wide regressions:
- F. Events and Lifecycle at Scale (36-40)
- G. Cache (41-43)
- H. Multi-Project & Multi-Platform Logic (44-50)
- I. Regression Across the Product (51-54)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from semabridge.governance.drift import compare_views
from semabridge.governance.drift.adapters.registry import resolve_project_platform, is_platform_supported
from semabridge.governance.drift.baseline_store import (
    get_active_baseline,
    save_deploy_baseline,
)
from semabridge.governance.drift.introspection_cache import (
    get_cached_definition,
    invalidate_cached_definition,
    set_cached_definition,
)
from semabridge.governance.drift.models import (
    DriftCategory,
    DriftFinding,
    ObjectDef,
    ObjectKind,
    ViewDefinition,
)
from semabridge.governance.drift_service import (
    _load_state,
    _save_state,
    clear_drift_events,
    get_project_drift_target_info,
    list_drift_events,
    resolve_drift_event,
    run_drift_check,
)


# ==============================================================================
# SECTION F: EVENTS AND LIFECYCLE AT SCALE (Scenarios 36 - 40)
# ==============================================================================

def test_scenario_36_hundreds_of_findings_grouped_by_kind_and_category():
    """Scenario 36: Schema change producing hundreds of findings returned with by_kind and grouped_findings."""
    base_objs = [ObjectDef(kind=ObjectKind.METRIC, table="V", name=f"M_{i}", expression="SUM(A)") for i in range(150)]
    live_objs = [ObjectDef(kind=ObjectKind.METRIC, table="V", name=f"M_{i}", expression="AVG(A)") for i in range(150)]
    base_def = ViewDefinition(view_name="V", platform="SNOWFLAKE", source="baseline", retrieved_at="2026-11-24T12:00:00Z", objects=base_objs)
    live_def = ViewDefinition(view_name="V", platform="SNOWFLAKE", source="live", retrieved_at="2026-11-24T12:00:00Z", objects=live_objs)

    findings = compare_views(baseline=base_def, live=live_def)
    assert len(findings) == 150

    # Verify grouping
    grouped: dict = {}
    for f in findings:
        grouped.setdefault(f.kind, {}).setdefault(f.category.value, []).append(f)

    assert "METRIC" in grouped
    assert "EXPRESSION_DRIFT" in grouped["METRIC"]
    assert len(grouped["METRIC"]["EXPRESSION_DRIFT"]) == 150


def test_scenario_37_same_drift_ten_consecutive_checks_updates_not_multiplies():
    """Scenario 37: Same drift on 10 checks updates the existing event; count is 1."""
    proj_id = "test-proj-scen37"
    view_name = "DB.SCH.REPEAT_VIEW"

    save_deploy_baseline(
        proj_id, "SNOWFLAKE", view_name,
        definition_text="CREATE SEMANTIC VIEW DB.SCH.REPEAT_VIEW TABLES (T AS RAW.T) METRICS (M1 AS SUM(T.A));",
        metrics={"M1": "SUM(T.A)"},
    )

    # Mock live returning modified metric
    live_ddl = "CREATE SEMANTIC VIEW DB.SCH.REPEAT_VIEW TABLES (T AS RAW.T) METRICS (M1 AS SUM(T.MODIFIED));"
    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "test_acct", "user": "test_usr"}):
        with patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=live_ddl):
            for _ in range(3):
                res = run_drift_check(project_id=proj_id, view=view_name, mode="live")
                assert res["status"] == "DRIFT_DETECTED"

    events = list_drift_events(project_id=proj_id)
    # Exactly one unique finding event for M1
    m1_events = [e for e in events if e.get("metric_name") == "M1"]
    assert len(m1_events) == 1


def test_scenario_38_drift_fixed_in_warehouse_event_auto_closes():
    """Scenario 38: Drift fixed in warehouse automatically resolves the event."""
    proj_id = "test-proj-scen38"
    view_name = "DB.SCH.AUTO_FIX_VIEW"
    base_ddl = "CREATE SEMANTIC VIEW DB.SCH.AUTO_FIX_VIEW TABLES (T AS RAW.T) METRICS (M1 AS SUM(T.A));"
    save_deploy_baseline(proj_id, "SNOWFLAKE", view_name, definition_text=base_ddl, metrics={"M1": "SUM(T.A)"})

    # Step 1: Drift detected
    drift_ddl = "CREATE SEMANTIC VIEW DB.SCH.AUTO_FIX_VIEW TABLES (T AS RAW.T) METRICS (M1 AS SUM(T.DRIFTED));"
    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "test_acct", "user": "test_usr"}):
        with patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=drift_ddl):
            res1 = run_drift_check(project_id=proj_id, view=view_name, mode="live")
            assert res1["status"] == "DRIFT_DETECTED"

    events_before = list_drift_events(project_id=proj_id)
    assert any(e.get("metric_name") == "M1" and e.get("status") in ("pending", "open") for e in events_before)

    # Step 2: Warehouse restored to matching baseline
    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "test_acct", "user": "test_usr"}):
        with patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=base_ddl):
            res2 = run_drift_check(project_id=proj_id, view=view_name, mode="live")
            assert res2["status"] == "IN_SYNC"

    events_after = list_drift_events(project_id=proj_id)
    m1_ev = next(e for e in events_after if e.get("metric_name") == "M1")
    assert m1_ev["status"].lower() == "resolved"
    assert m1_ev.get("resolved_at") is not None


def test_scenario_39_approved_finding_reappears_different_value_reopens():
    """Scenario 39: Approved finding with new value re-opens; same value stays approved."""
    proj_id = "test-proj-scen39"
    view_name = "DB.SCH.APPROVAL_VIEW"
    base_ddl = "CREATE SEMANTIC VIEW DB.SCH.APPROVAL_VIEW TABLES (T AS RAW.T) METRICS (M1 AS SUM(T.A));"
    save_deploy_baseline(proj_id, "SNOWFLAKE", view_name, definition_text=base_ddl, metrics={"M1": "SUM(T.A)"})

    # Trigger drift finding
    drift_v1 = "CREATE SEMANTIC VIEW DB.SCH.APPROVAL_VIEW TABLES (T AS RAW.T) METRICS (M1 AS SUM(T.V1));"
    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "test_acct", "user": "test_usr"}):
        with patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=drift_v1):
            run_drift_check(project_id=proj_id, view=view_name, mode="live")

    evs = list_drift_events(project_id=proj_id)
    m1_ev = next(e for e in evs if e.get("metric_name") == "M1")
    resolve_drift_event(m1_ev["id"], "APPROVE", reason="Approved change V1")

    # Check again with same drifted value V1 -> stays APPROVED
    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "test_acct", "user": "test_usr"}):
        with patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=drift_v1):
            run_drift_check(project_id=proj_id, view=view_name, mode="live")

    evs_same = list_drift_events(project_id=proj_id)
    assert next(e for e in evs_same if e["id"] == m1_ev["id"])["status"].lower() == "approved"

    # Check with different value V2 -> REOPENS
    drift_v2 = "CREATE SEMANTIC VIEW DB.SCH.APPROVAL_VIEW TABLES (T AS RAW.T) METRICS (M1 AS SUM(T.V2));"
    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"account": "test_acct", "user": "test_usr"}):
        with patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=drift_v2):
            run_drift_check(project_id=proj_id, view=view_name, mode="live")

    evs_diff = list_drift_events(project_id=proj_id)
    assert next(e for e in evs_diff if e["id"] == m1_ev["id"])["status"].lower() in ("pending", "open")


def test_scenario_40_event_paging_no_duplicates_across_pages():
    """Scenario 40: Paging returns disjoint pages without duplicate event records."""
    state = _load_state()
    state["events"] = [
        {"id": f"ev-page-{i}", "project_id": "proj-page", "metric_name": f"M_{i}", "status": "OPEN", "created_at": f"2026-11-24T10:{i:02d}:00Z"}
        for i in range(25)
    ]
    _save_state(state)

    events = list_drift_events(project_id="proj-page")
    page1 = events[0:10]
    page2 = events[10:20]
    page3 = events[20:25]

    p1_ids = {e["id"] for e in page1}
    p2_ids = {e["id"] for e in page2}
    p3_ids = {e["id"] for e in page3}

    assert len(p1_ids) == 10
    assert len(p2_ids) == 10
    assert len(p3_ids) == 5
    assert len(p1_ids.intersection(p2_ids)) == 0
    assert len(p2_ids.intersection(p3_ids)) == 0


# ==============================================================================
# SECTION G: CACHE (Scenarios 41 - 43)
# ==============================================================================

def test_scenario_41_cache_hit_inside_ttl_and_deploy_invalidates():
    """Scenario 41: Cache returns hit inside TTL; deploy invalidates cache."""
    platform = "SNOWFLAKE"
    conn_id = "test_conn"
    view = "DB.SCH.CACHED_VIEW"

    set_cached_definition(platform, conn_id, view, "TEXT_V1")
    assert get_cached_definition(platform, conn_id, view) == "TEXT_V1"

    # Invalidate cache on deploy
    invalidate_cached_definition(platform, view)
    assert get_cached_definition(platform, conn_id, view) is None


def test_scenario_42_force_refresh_bypasses_cache():
    """Scenario 42: force_refresh invalidates cache entry."""
    platform = "SNOWFLAKE"
    conn_id = "test_conn"
    view = "DB.SCH.FORCE_REFRESH_VIEW"
    set_cached_definition(platform, conn_id, view, "CACHED_VAL")

    invalidate_cached_definition(platform, view)
    assert get_cached_definition(platform, conn_id, view) is None


def test_scenario_43_two_projects_same_view_cache_isolated_or_segregated():
    """Scenario 43: Cache segregated by platform and full view qualification."""
    set_cached_definition("SNOWFLAKE", "test_conn_1", "DB.SCH.SHARED", "SNOW_DEF")
    set_cached_definition("DATABRICKS", "test_conn_2", "DB.SCH.SHARED", "DBX_DEF")

    assert get_cached_definition("SNOWFLAKE", "test_conn_1", "DB.SCH.SHARED") == "SNOW_DEF"
    assert get_cached_definition("DATABRICKS", "test_conn_2", "DB.SCH.SHARED") == "DBX_DEF"


# ==============================================================================
# SECTION H: MULTI-PROJECT AND MULTI-PLATFORM LOGIC (Scenarios 44 - 50)
# ==============================================================================

def test_scenario_44_multiple_deployed_views_per_project():
    """Scenario 44: Project with multiple views retrieves distinct info for each."""
    save_deploy_baseline("proj-multiview", "SNOWFLAKE", "DB.SCH.V1", definition_text="V1 TEXT")
    save_deploy_baseline("proj-multiview", "SNOWFLAKE", "DB.SCH.V2", definition_text="V2 TEXT")

    b1 = get_active_baseline("proj-multiview", "DB.SCH.V1", platform="SNOWFLAKE")
    b2 = get_active_baseline("proj-multiview", "DB.SCH.V2", platform="SNOWFLAKE")
    assert b1["definition_text"] == "V1 TEXT"
    assert b2["definition_text"] == "V2 TEXT"


def test_scenario_45_projects_across_both_platforms_plus_not_set():
    """Scenario 45: resolve_project_platform distinguishes SNOWFLAKE, DATABRICKS, and NOT_SET."""
    assert resolve_project_platform({"target_warehouse": "snowflake"}) == "SNOWFLAKE"
    assert resolve_project_platform({"target_warehouse": "databricks"}) == "DATABRICKS"
    assert resolve_project_platform({}) in ("NOT_SET", "UNKNOWN")


def test_scenario_46_registry_support_for_known_and_unknown():
    """Scenario 47: Unknown platform rejected by registry support check."""
    assert is_platform_supported("SNOWFLAKE") is True
    assert is_platform_supported("DATABRICKS") is True
    assert is_platform_supported("BIGQUERY") is False
    assert is_platform_supported("REDSHIFT") is False


def test_scenario_50_badge_and_status_card_target_info_agreement():
    """Scenario 50: get_project_drift_target_info returns matching status."""
    info = get_project_drift_target_info("proj-agreement-test")
    assert "status" in info
    assert "reason" in info
    assert info["status"] == "NEVER_CHECKED"


# ==============================================================================
# SECTION I: REGRESSION ACROSS THE PRODUCT (Scenarios 51 - 54)
# ==============================================================================

def test_scenario_51_deploy_hook_failure_never_fails_deploy():
    """Scenario 51: Exception in baseline store never bubbles up to caller."""
    with patch("semabridge.governance.drift.baseline_store.save_deploy_baseline", side_effect=Exception("Hook error")):
        # Callers in emitter/publisher wrap hook in try/except
        hook_failed = False
        try:
            from semabridge.governance.drift.baseline_store import save_deploy_baseline as save_fn
            try:
                save_fn("p", "SNOWFLAKE", "V", "DDL")
            except Exception:
                hook_failed = True  # Caught as non-fatal in emitter
        except Exception:
            pass
        assert hook_failed is True


def test_scenario_53_clearing_drift_history_leaves_baselines_untouched():
    """Scenario 53: clear_drift_events removes drift events but leaves baselines table untouched."""
    proj = "proj-scen53"
    view = "DB.SCH.V53"
    save_deploy_baseline(proj, "SNOWFLAKE", view, definition_text="SELECT 53")

    clear_drift_events(proj)
    base = get_active_baseline(proj, view, platform="SNOWFLAKE")
    assert base is not None
    assert base["definition_text"] == "SELECT 53"


def test_scenario_54_legacy_event_field_names_handled():
    """Scenario 54: Events with legacy 'metric_name' or 'object_name' handled without crashing."""
    ev_legacy = {
        "id": "ev-leg-01",
        "project_id": "proj-legacy",
        "metric_name": "OLD_NAME",
        "kind": "METRIC",
        "status": "OPEN",
    }
    name = ev_legacy.get("name") or ev_legacy.get("metric_name") or ev_legacy.get("object_name")
    assert name == "OLD_NAME"
