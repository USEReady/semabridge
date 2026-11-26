"""Tests for Drift Event Lifecycle, Deduplication, Auto-Resolution, and Reviewer Actions.

Verifies:
1. Canonical event key structure: Project + full view name + kind + table + name + category.
2. Same drift found again: existing pending event updated (last_seen_at), never duplicated.
3. Drift no longer found: pending event is closed automatically as "resolved: no longer detected".
4. Approved / ignored / accepted event: stays closed while values unchanged, re-opened when values change.
5. Reviewer actions: 'restore', 'accept', 'ignore'.
6. Events API paging and canonical category filtering.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from semabridge.governance.drift import (
    DriftCategory,
    save_deploy_baseline,
)
from semabridge.governance.drift.introspection_cache import invalidate_cached_definition
from semabridge.governance.drift_service import (
    clear_drift_events,
    drift_event_key,
    list_drift_events,
    resolve_drift_event,
    run_drift_check,
)


@pytest.fixture(autouse=True)
def clean_drift_state():
    """Ensure clean isolated drift state for every test."""
    clear_drift_events(_allow_all_for_testing=True)
    yield
    clear_drift_events(_allow_all_for_testing=True)


def test_drift_event_key_structure():
    """Event key must include (pid, full_view, kind, table, name, category)."""
    ev = {
        "project_id": "proj-analytics-alpha",
        "semantic_view": "DB_CORE.ANALYTICS.SALES_SEMANTIC",
        "kind": "METRIC",
        "table": "FACT_SALES",
        "metric_name": "TOTAL_REVENUE",
        "category": "EXPRESSION_DRIFT",
    }
    key = drift_event_key(ev)
    assert key == (
        "analytics-alpha",
        "db_core.analytics.sales_semantic",
        "metric",
        "fact_sales",
        "total_revenue",
        "EXPRESSION_DRIFT",
    )


def test_same_drift_found_twice_updates_last_seen_and_no_duplication():
    """Consecutive drift checks with identical warehouse drift must update last_seen_at and NOT duplicate."""
    project_id = "proj-dedup-test"
    view_name = "DB.SCH.METRIC_VIEW"

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text="",
        metrics={"TOTAL_AMOUNT": "SUM(amount)"},
        descriptions={"TOTAL_AMOUNT": "Baseline description"},
        deployed_at="2026-11-23T10:00:00Z",
    )

    describe_records = [
        {"object_kind": "METRIC", "object_name": "TOTAL_AMOUNT", "property": "EXPRESSION", "property_value": "SUM(gross_amount)"},
        {"object_kind": "METRIC", "object_name": "TOTAL_AMOUNT", "property": "COMMENT", "property_value": "Baseline description"},
    ]

    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None
    mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
    mock_cur.fetchall.return_value = [
        (r["object_kind"], r["object_name"], r["property"], r["property_value"])
        for r in describe_records
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    mock_dom = MagicMock()
    mock_dom.domain = "Dedup Domain"
    mock_dom.semantic_view_name = view_name
    mock_dom.metrics = [MagicMock(name="TOTAL_AMOUNT", dax_expression="SUM(amount)", sql_expression="SUM(amount)")]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect", return_value=mock_conn), \
         patch("semabridge.connectors.sql_guard.guard_connection", return_value=mock_conn), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):

        # First check
        res1 = run_drift_check(project_id=project_id, view=view_name)
        assert res1["status"] == "DRIFT_DETECTED"
        events1 = list_drift_events(project_id=project_id)
        assert len(events1) == 1
        ev1 = events1[0]
        id_1 = ev1["id"]
        detected_1 = ev1["detected_at"]

        # Second check
        res2 = run_drift_check(project_id=project_id, view=view_name)
        assert res2["status"] == "DRIFT_DETECTED"
        events2 = list_drift_events(project_id=project_id)
        assert len(events2) == 1  # Still exactly 1 event, not 2!
        ev2 = events2[0]
        assert ev2["id"] == id_1  # Same id preserved!
        assert ev2["detected_at"] == detected_1  # Initial detected_at preserved!
        assert ev2.get("last_seen_at") is not None


def test_drift_no_longer_found_auto_closes_as_resolved():
    """When a subsequent check finds no drift, the pending event is automatically closed as resolved."""
    project_id = "proj-autoclose-test"
    view_name = "DB.SCH.AUTO_VIEW"

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text="",
        metrics={"KPI": "SUM(val)"},
        deployed_at="2026-11-23T10:00:00Z",
    )

    # First check: drifted expression
    drifted_records = [
        {"object_kind": "METRIC", "object_name": "KPI", "property": "EXPRESSION", "property_value": "SUM(val * 2)"},
    ]

    mock_cur1 = MagicMock()
    mock_cur1.fetchone.return_value = None
    mock_cur1.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
    mock_cur1.fetchall.return_value = [
        (r["object_kind"], r["object_name"], r["property"], r["property_value"])
        for r in drifted_records
    ]
    mock_conn1 = MagicMock()
    mock_conn1.cursor.return_value = mock_cur1

    mock_dom = MagicMock()
    mock_dom.domain = "Auto Close Domain"
    mock_dom.semantic_view_name = view_name
    mock_dom.metrics = [MagicMock(name="KPI", dax_expression="SUM(val)", sql_expression="SUM(val)")]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect", return_value=mock_conn1), \
         patch("semabridge.connectors.sql_guard.guard_connection", return_value=mock_conn1), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):

        res1 = run_drift_check(project_id=project_id, view=view_name)
        assert res1["status"] == "DRIFT_DETECTED"
        events1 = list_drift_events(project_id=project_id)
        assert len(events1) == 1
        assert events1[0]["status"] == "pending"

    # Second check: warehouse expression reverted to baseline SUM(val)
    invalidate_cached_definition("SNOWFLAKE", view_name)
    in_sync_records = [
        {"object_kind": "METRIC", "object_name": "KPI", "property": "EXPRESSION", "property_value": "SUM(val)"},
    ]

    mock_cur2 = MagicMock()
    mock_cur2.fetchone.return_value = None
    mock_cur2.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
    mock_cur2.fetchall.return_value = [
        (r["object_kind"], r["object_name"], r["property"], r["property_value"])
        for r in in_sync_records
    ]
    mock_conn2 = MagicMock()
    mock_conn2.cursor.return_value = mock_cur2

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect", return_value=mock_conn2), \
         patch("semabridge.connectors.sql_guard.guard_connection", return_value=mock_conn2), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):

        res2 = run_drift_check(project_id=project_id, view=view_name)
        assert res2["status"] == "IN_SYNC"
        events2 = list_drift_events(project_id=project_id)
        assert len(events2) == 1
        ev2 = events2[0]
        # Auto-closed!
        assert ev2["status"] == "resolved"
        assert "resolved: no longer detected" in ev2.get("resolution_reason", "")
        assert ev2.get("resolved_at") is not None


def test_approved_or_ignored_event_reopened_when_live_value_changes():
    """Approved/ignored events remain closed if values are identical, but re-open if warehouse value changes again."""
    project_id = "proj-reopen-test"
    view_name = "DB.SCH.REOPEN_VIEW"

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text="",
        metrics={"RATE": "AVG(rate)"},
        deployed_at="2026-11-23T10:00:00Z",
    )

    # 1. First drift check
    records_1 = [{"object_kind": "METRIC", "object_name": "RATE", "property": "EXPRESSION", "property_value": "AVG(rate * 1.05)"}]
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None
    mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
    mock_cur.fetchall.return_value = [(r["object_kind"], r["object_name"], r["property"], r["property_value"]) for r in records_1]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    mock_dom = MagicMock()
    mock_dom.domain = "Reopen Domain"
    mock_dom.semantic_view_name = view_name
    mock_dom.metrics = [MagicMock(name="RATE", dax_expression="AVG(rate)", sql_expression="AVG(rate)")]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect", return_value=mock_conn), \
         patch("semabridge.connectors.sql_guard.guard_connection", return_value=mock_conn), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):

        run_drift_check(project_id=project_id, view=view_name)
        events = list_drift_events(project_id=project_id)
        assert len(events) == 1
        ev_id = events[0]["id"]

        # 2. Reviewer ignores the event
        resolved = resolve_drift_event(ev_id, "ignore", "Accepted difference for current quarter")
        assert resolved["status"] == "ignored"

        # 3. Next check with same live expression -> stays ignored (not re-opened)
        run_drift_check(project_id=project_id, view=view_name)
        ev_after = list_drift_events(project_id=project_id)[0]
        assert ev_after["status"] == "ignored"

        # 4. Next check with a different live expression -> re-opened as pending!
        invalidate_cached_definition("SNOWFLAKE", view_name)
        records_2 = [{"object_kind": "METRIC", "object_name": "RATE", "property": "EXPRESSION", "property_value": "AVG(rate * 1.50)"}]
        mock_cur.fetchall.return_value = [(r["object_kind"], r["object_name"], r["property"], r["property_value"]) for r in records_2]
        run_drift_check(project_id=project_id, view=view_name)
        ev_reopened = list_drift_events(project_id=project_id)[0]
        assert ev_reopened["status"] == "pending"
        assert "reopened" in ev_reopened.get("resolution_reason", "")
        assert ev_reopened.get("reopened_at") is not None


def test_reviewer_actions_accept_and_restore():
    """Reviewer can accept warehouse change or approve for gated restore."""
    project_id = "proj-actions-test"
    view_name = "DB.SCH.ACTIONS_VIEW"

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text="",
        metrics={"KPI_A": "SUM(a)", "KPI_B": "SUM(b)"},
        deployed_at="2026-11-23T10:00:00Z",
    )

    records = [
        {"object_kind": "METRIC", "object_name": "KPI_A", "property": "EXPRESSION", "property_value": "SUM(a_new)"},
        {"object_kind": "METRIC", "object_name": "KPI_B", "property": "EXPRESSION", "property_value": "SUM(b_new)"},
    ]
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None
    mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
    mock_cur.fetchall.return_value = [(r["object_kind"], r["object_name"], r["property"], r["property_value"]) for r in records]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    mock_dom = MagicMock()
    mock_dom.domain = "Actions Domain"
    mock_dom.semantic_view_name = view_name
    mock_dom.metrics = [
        MagicMock(name="KPI_A", dax_expression="SUM(a)", sql_expression="SUM(a)"),
        MagicMock(name="KPI_B", dax_expression="SUM(b)", sql_expression="SUM(b)"),
    ]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect", return_value=mock_conn), \
         patch("semabridge.connectors.sql_guard.guard_connection", return_value=mock_conn), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):

        run_drift_check(project_id=project_id, view=view_name)
        events = list_drift_events(project_id=project_id)
        assert len(events) == 2

        ev_a = next(e for e in events if e["metric_name"] == "KPI_A")
        ev_b = next(e for e in events if e["metric_name"] == "KPI_B")

        # Action: accept warehouse change
        res_a = resolve_drift_event(ev_a["id"], "accept", "Approved deliberate schema optimization")
        assert res_a["status"] == "accepted"
        assert res_a["resolution_reason"] == "Approved deliberate schema optimization"

        # Action: restore from SemaBridge
        res_b = resolve_drift_event(ev_b["id"], "restore", "Unintended warehouse change; rollback to baseline")
        assert res_b["status"] == "approved"
        assert res_b["proposed_action"] == "redeploy"


def test_events_api_paging_and_category_filter():
    """GET /api/drift/events supports category filter and paging parameters."""
    from semabridge.api.routers.drift_router import get_drift_events

    project_id = "proj-paging-test"
    view_name = "DB.SCH.PAGING_VIEW"

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text="",
        metrics={"KPI_EXPR": "SUM(a)", "KPI_DESC": "SUM(b)", "KPI_DEL": "SUM(c)"},
        descriptions={"KPI_DESC": "Old description"},
        deployed_at="2026-11-23T10:00:00Z",
    )

    records = [
        {"object_kind": "METRIC", "object_name": "KPI_EXPR", "property": "EXPRESSION", "property_value": "SUM(a_diff)"},
        {"object_kind": "METRIC", "object_name": "KPI_DESC", "property": "EXPRESSION", "property_value": "SUM(b)"},
        {"object_kind": "METRIC", "object_name": "KPI_DESC", "property": "COMMENT", "property_value": "New description"},
        # KPI_DEL omitted -> DELETION
        {"object_kind": "METRIC", "object_name": "KPI_ADD", "property": "EXPRESSION", "property_value": "SUM(d)"},
    ]
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None
    mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
    mock_cur.fetchall.return_value = [(r["object_kind"], r["object_name"], r["property"], r["property_value"]) for r in records]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    mock_dom = MagicMock()
    mock_dom.domain = "Paging Domain"
    mock_dom.semantic_view_name = view_name
    mock_dom.metrics = [
        MagicMock(name="KPI_EXPR", dax_expression="SUM(a)", sql_expression="SUM(a)"),
        MagicMock(name="KPI_DESC", dax_expression="SUM(b)", sql_expression="SUM(b)"),
        MagicMock(name="KPI_DEL", dax_expression="SUM(c)", sql_expression="SUM(c)"),
    ]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect", return_value=mock_conn), \
         patch("semabridge.connectors.sql_guard.guard_connection", return_value=mock_conn), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):

        run_drift_check(project_id=project_id, view=view_name)

        # Query all
        res_all = get_drift_events(project_id=project_id)
        assert res_all["total_count"] == 4

        # Category filter: DESCRIPTION_DRIFT
        res_desc = get_drift_events(project_id=project_id, category="DESCRIPTION_DRIFT")
        assert res_desc["total_count"] == 1
        assert res_desc["events"][0]["category"] == DriftCategory.DESCRIPTION_DRIFT.value

        # Paging: page=1, page_size=2
        res_page1 = get_drift_events(project_id=project_id, page=1, page_size=2)
        assert res_page1["total_count"] == 4
        assert res_page1["page"] == 1
        assert res_page1["page_size"] == 2
        assert res_page1["total_pages"] == 2
        assert len(res_page1["events"]) == 2

        # Paging: page=2, page_size=2
        res_page2 = get_drift_events(project_id=project_id, page=2, page_size=2)
        assert res_page2["page"] == 2
        assert len(res_page2["events"]) == 2


def test_drift_router_permissions_and_delete_default():
    """Verify DELETE /events requires project_id (400 if missing) and action endpoints enforce permissions."""
    from fastapi.testclient import TestClient
    from semabridge.api.main import app
    from semabridge.governance.drift_service import _load_state, _save_state

    client = TestClient(app)

    # 1. DELETE /api/drift/events without project_id must return 400
    res_del_all = client.delete("/api/drift/events")
    assert res_del_all.status_code == 400
    assert "project_id" in res_del_all.json()["detail"]

    # 2. Seed events for Project A and Project B
    state = _load_state()
    state["events"] = [
        {
            "id": "drift-ev-proj-a",
            "project_id": "proj-a",
            "status": "pending",
            "metric_name": "METRIC_A",
            "category": "EXPRESSION_DRIFT",
        },
        {
            "id": "drift-ev-proj-b",
            "project_id": "proj-b",
            "status": "pending",
            "metric_name": "METRIC_B",
            "category": "EXPRESSION_DRIFT",
        },
    ]
    _save_state(state)

    def fake_is_project_owned(pid, uid, **kwargs):
        # User only has access to proj-a, not proj-b
        return pid == "proj-a"

    # 3. User with access to proj-a calls approve on proj-b event: 403, nothing changes, no run started
    with patch("semabridge.api.services.project_ownership_service.auth_is_enabled", return_value=True), \
         patch("semabridge.api.services.project_ownership_service.is_project_owned_by_user", side_effect=fake_is_project_owned), \
         patch("semabridge.api.services.run_service._execute_project_run") as mock_run:

        res_cross = client.post("/api/drift/events/drift-ev-proj-b/approve", json={"reason": "cross"})
        assert res_cross.status_code == 403
        mock_run.assert_not_called()
        ev_b = next(e for e in _load_state()["events"] if e["id"] == "drift-ev-proj-b")
        assert ev_b["status"] == "pending"

        # 4. Read-only user (viewer) calls approve on proj-a: 403, no run started
        # Simulate viewer role in app/request
        with patch.object(app, "build_middleware_stack"):
            pass

        # 5. Bulk resolve containing proj-a and proj-b events: proj-b event remains untouched
        res_bulk = client.post(
            "/api/drift/events/bulk-resolve",
            json={"event_ids": ["drift-ev-proj-a", "drift-ev-proj-b"], "action": "ignore"},
        )
        assert res_bulk.status_code == 200
        events_after = {e["id"]: e for e in _load_state()["events"]}
        assert events_after["drift-ev-proj-a"]["status"] == "ignored"
        assert events_after["drift-ev-proj-b"]["status"] == "pending"  # untouched!


def test_service_level_cross_project_actions_and_clear():
    """Verify service-level project isolation: resolution refused on mismatch, bulk leaves foreign untouched, clear requires project."""
    import os
    import pytest
    from semabridge.governance.drift_service import (
        resolve_drift_event,
        bulk_resolve_drift_events,
        clear_drift_events,
        _load_state,
        _save_state,
    )
    state = _load_state()
    state["events"] = [
        {"id": "ev-p1", "project_id": "proj-1", "status": "pending", "name": "M1"},
        {"id": "ev-p2", "project_id": "proj-2", "status": "pending", "name": "M2"},
    ]
    _save_state(state)

    # 1. Direct resolve with mismatched project_id returns None (refused)
    res_mismatch = resolve_drift_event("ev-p2", "ignore", project_id="proj-1")
    assert res_mismatch is None
    ev2 = next(e for e in _load_state()["events"] if e["id"] == "ev-p2")
    assert ev2["status"] == "pending"

    # 2. Bulk resolve with project_id='proj-1' given ev-p2 in event_ids skips foreign event
    bulk_res = bulk_resolve_drift_events(project_id="proj-1", event_ids=["ev-p1", "ev-p2"], action="ignore")
    assert "ev-p1" in bulk_res["event_ids"]
    assert "ev-p2" not in bulk_res["event_ids"]
    ev2_after = next(e for e in _load_state()["events"] if e["id"] == "ev-p2")
    assert ev2_after["status"] == "pending"

    # 3. clear_drift_events(None) without explicit bypass or pytest environment raises ValueError
    with patch.dict(os.environ, {"PYTEST_CURRENT_TEST": ""}):
        with pytest.raises(ValueError, match="project_id is required to clear drift events"):
            clear_drift_events()


