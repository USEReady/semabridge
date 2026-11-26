from __future__ import annotations

from unittest.mock import patch
import pytest
from semabridge.api.routers.drift_router import get_drift_events


def _build_test_events(project_id: str, view_name: str):
    """Builds 58 events matching the user prompt scenario:
    Total = 58 (6 pending, 52 accepted/resolved)
    Categories:
      ADDITION: 18 (1 pending, 17 resolved)
      EXPRESSION_DRIFT: 38 (4 pending, 34 resolved)
      DESCRIPTION_DRIFT: 2 (1 pending, 1 resolved)
      DELETION: 0
    """
    events = []
    # 1. ADDITION: 18
    events.append({
        "id": "add-p-1",
        "project_id": project_id,
        "semantic_view": view_name,
        "kind": "METRIC",
        "table": "TBL",
        "metric_name": "ADD_PEND",
        "category": "ADDITION",
        "status": "pending",
        "mode": "live",
    })
    for i in range(17):
        events.append({
            "id": f"add-r-{i}",
            "project_id": project_id,
            "semantic_view": view_name,
            "kind": "METRIC",
            "table": "TBL",
            "metric_name": f"ADD_RES_{i}",
            "category": "ADDITION",
            "status": "accepted",
            "mode": "live",
        })

    # 2. EXPRESSION_DRIFT: 38
    for i in range(4):
        events.append({
            "id": f"expr-p-{i}",
            "project_id": project_id,
            "semantic_view": view_name,
            "kind": "METRIC",
            "table": "TBL",
            "metric_name": f"EXPR_PEND_{i}",
            "category": "EXPRESSION_DRIFT",
            "status": "pending",
            "mode": "live",
        })
    for i in range(34):
        events.append({
            "id": f"expr-r-{i}",
            "project_id": project_id,
            "semantic_view": view_name,
            "kind": "METRIC",
            "table": "TBL",
            "metric_name": f"EXPR_RES_{i}",
            "category": "EXPRESSION_DRIFT",
            "status": "accepted",
            "mode": "live",
        })

    # 3. DESCRIPTION_DRIFT: 2
    events.append({
        "id": "desc-p-1",
        "project_id": project_id,
        "semantic_view": view_name,
        "kind": "METRIC",
        "table": "TBL",
        "metric_name": "DESC_PEND",
        "category": "DESCRIPTION_DRIFT",
        "status": "pending",
        "mode": "live",
    })
    events.append({
        "id": "desc-r-1",
        "project_id": project_id,
        "semantic_view": view_name,
        "kind": "METRIC",
        "table": "TBL",
        "metric_name": "DESC_RES",
        "category": "DESCRIPTION_DRIFT",
        "status": "accepted",
        "mode": "live",
    })
    return events


def test_drift_counts_agreement_status_all_and_pending():
    """THE RULE:
    Every number on the page comes from one response, counting the currently selected filters.
    - Status filter on 'all': header, category chips, panel title, and summary all count 58.
    - Status filter on 'pending': all of them count 6.
    - Changing a filter changes every number together.
    - Exception: Status chips show the breakdown within current category/kind filters.
    - Dead fields (pending_by_category, pending_by_kind) are deleted.
    """
    project_id = "proj-rule-test"
    view_name = "DB.SCH.RULE_VIEW"
    seeded_events = _build_test_events(project_id, view_name)

    with patch("semabridge.api.routers.drift_router.list_drift_events", return_value=seeded_events), \
         patch("semabridge.api.routers.drift_router.get_project_drift_target_info", return_value={}):

        # ── Test 1: STATUS FILTER ON "ALL" ──────────────────────────────────────────
        res_all = get_drift_events(project_id=project_id, status="all")

        # Cleaned up fields:
        assert "pending_by_category" not in res_all, "pending_by_category must be removed"
        assert "pending_by_kind" not in res_all, "pending_by_kind must be removed"
        assert res_all["pending_count"] == 6

        # All numbers must count 58:
        assert res_all["total_count"] == 58
        assert res_all["total_unfiltered_count"] == 58
        assert res_all["counts_by_status"]["all"] == 58

        # Category chips sum to 58:
        cats_all = res_all["counts_by_category"]
        assert cats_all.get("ADDITION") == 18
        assert cats_all.get("EXPRESSION_DRIFT") == 38
        assert cats_all.get("DESCRIPTION_DRIFT") == 2
        assert cats_all.get("DELETION", 0) == 0
        assert sum(cats_all.values()) == 58

        # Status chips show status distribution across the active category/kind scope:
        assert res_all["counts_by_status"]["pending"] == 6
        assert res_all["counts_by_status"]["accepted"] == 52

        # ── Test 2: STATUS FILTER ON "PENDING" ──────────────────────────────────────
        res_pending = get_drift_events(project_id=project_id, status="pending")

        # Cleaned up fields:
        assert "pending_by_category" not in res_pending
        assert "pending_by_kind" not in res_pending
        assert res_pending["pending_count"] == 6

        # All numbers must count 6:
        assert res_pending["total_count"] == 6
        assert res_pending["counts_by_status"]["pending"] == 6

        # Category chips sum to 6:
        cats_pending = res_pending["counts_by_category"]
        assert cats_pending.get("ADDITION") == 1
        assert cats_pending.get("EXPRESSION_DRIFT") == 4
        assert cats_pending.get("DESCRIPTION_DRIFT") == 1
        assert sum(cats_pending.values()) == 6

        # Status chips still show the status distribution across category/kind:
        assert res_pending["counts_by_status"]["all"] == 58
        assert res_pending["counts_by_status"]["accepted"] == 52


def test_status_chips_scope_to_selected_category():
    """Status chips show the breakdown within the currently selected category."""
    project_id = "proj-cat-test"
    view_name = "DB.SCH.CAT_VIEW"

    events = [
        # 3 ADDITION (1 pending, 2 accepted)
        {"id": "a1", "project_id": project_id, "semantic_view": view_name, "kind": "METRIC", "table": "T", "metric_name": "A1", "category": "ADDITION", "status": "pending", "mode": "live"},
        {"id": "a2", "project_id": project_id, "semantic_view": view_name, "kind": "METRIC", "table": "T", "metric_name": "A2", "category": "ADDITION", "status": "accepted", "mode": "live"},
        {"id": "a3", "project_id": project_id, "semantic_view": view_name, "kind": "METRIC", "table": "T", "metric_name": "A3", "category": "ADDITION", "status": "accepted", "mode": "live"},
        # 5 EXPRESSION_DRIFT (all pending)
        {"id": "e1", "project_id": project_id, "semantic_view": view_name, "kind": "METRIC", "table": "T", "metric_name": "E1", "category": "EXPRESSION_DRIFT", "status": "pending", "mode": "live"},
        {"id": "e2", "project_id": project_id, "semantic_view": view_name, "kind": "METRIC", "table": "T", "metric_name": "E2", "category": "EXPRESSION_DRIFT", "status": "pending", "mode": "live"},
        {"id": "e3", "project_id": project_id, "semantic_view": view_name, "kind": "METRIC", "table": "T", "metric_name": "E3", "category": "EXPRESSION_DRIFT", "status": "pending", "mode": "live"},
        {"id": "e4", "project_id": project_id, "semantic_view": view_name, "kind": "METRIC", "table": "T", "metric_name": "E4", "category": "EXPRESSION_DRIFT", "status": "pending", "mode": "live"},
        {"id": "e5", "project_id": project_id, "semantic_view": view_name, "kind": "METRIC", "table": "T", "metric_name": "E5", "category": "EXPRESSION_DRIFT", "status": "pending", "mode": "live"},
    ]

    with patch("semabridge.api.routers.drift_router.list_drift_events", return_value=events), \
         patch("semabridge.api.routers.drift_router.get_project_drift_target_info", return_value={}):

        # Scoped to ADDITION
        res_add = get_drift_events(project_id=project_id, category="ADDITION")
        assert res_add["total_count"] == 3
        assert res_add["counts_by_status"]["all"] == 3
        assert res_add["counts_by_status"]["pending"] == 1
        assert res_add["counts_by_status"]["accepted"] == 2
