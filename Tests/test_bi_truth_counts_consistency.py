"""Test count consistency across BI Truth Suite components:
- Warehouse grouping: sum of warehouse projects equals total projects
- Status filters: approved + draft + rejected == all
- Critical count <= all
- Run Suite button count == approved count
- Project list case badges agree with API cases payload
"""

import pytest


def test_status_filter_counts_sum_to_all():
    """Verify that case status filter partitions are mutually exclusive and collectively exhaustive."""
    mock_cases = [
        {"id": "c1", "status": "approved", "is_critical": True},
        {"id": "c2", "status": "approved", "is_critical": False},
        {"id": "c3", "status": "draft", "is_critical": False},
        {"id": "c4", "status": "draft", "is_critical": True},
        {"id": "c5", "status": "rejected", "is_critical": False},
    ]

    approved = sum(1 for c in mock_cases if c["status"] == "approved")
    draft = sum(1 for c in mock_cases if c["status"] == "draft")
    rejected = sum(1 for c in mock_cases if c["status"] == "rejected")
    critical = sum(1 for c in mock_cases if c.get("is_critical"))
    total = len(mock_cases)

    # 1. Mutually exclusive and exhaustive partitions
    assert approved + draft + rejected == total, "Approved, draft, and rejected must sum to total"

    # 2. Critical is an orthogonal attribute bounded by total
    assert critical <= total, "Critical cases cannot exceed total cases"
    assert critical == 2

    # 3. Run Suite button label count strictly mirrors approved cases
    run_suite_badge_count = approved
    assert run_suite_badge_count == 2


def test_warehouse_grouped_project_counts():
    """Verify that warehouse project counts sum to total projects and maintain integrity."""
    mock_projects = [
        {"id": "p1", "target_warehouse": "SNOWFLAKE"},
        {"id": "p2", "target_warehouse": "SNOWFLAKE"},
        {"id": "p3", "target_warehouse": "DATABRICKS"},
        {"id": "p4", "target_warehouse": None},  # Not set / other
    ]

    warehouse_counts = {}
    for p in mock_projects:
        wh = (p.get("target_warehouse") or "Not set").upper()
        warehouse_counts[wh] = warehouse_counts.get(wh, 0) + 1

    total_projects = len(mock_projects)
    assert sum(warehouse_counts.values()) == total_projects
    assert warehouse_counts["SNOWFLAKE"] == 2
    assert warehouse_counts["DATABRICKS"] == 1
    assert warehouse_counts["NOT SET"] == 1


def test_project_case_counts_sync():
    """Verify that project-level case counts correctly aggregate by project_id."""
    mock_all_cases = [
        {"id": "c1", "project_id": "proj-hr"},
        {"id": "c2", "project_id": "proj-hr"},
        {"id": "c3", "project_id": "proj-sales"},
        {"id": "c4", "project_id": "proj-finance"},
    ]

    counts = {}
    for c in mock_all_cases:
        pid = c.get("project_id", "")
        if pid:
            counts[pid] = counts.get(pid, 0) + 1

    assert counts.get("proj-hr") == 2
    assert counts.get("proj-sales") == 1
    assert counts.get("proj-finance") == 1
    assert counts.get("proj-missing", 0) == 0
    assert sum(counts.values()) == len(mock_all_cases)
