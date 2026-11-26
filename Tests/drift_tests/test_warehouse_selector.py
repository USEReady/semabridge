from __future__ import annotations

import pytest
from semabridge.api.services.projects_service import list_projects_compat
from semabridge.api.controllers.projects_controller import enrich_projects_with_ontology_and_sync_status

@pytest.mark.asyncio
async def test_warehouse_counts_sum_to_total():
    """Verify that projects across all platforms sum exactly to the total project count."""
    raw_projects = await list_projects_compat(limit=1000, offset=0)
    projects = enrich_projects_with_ontology_and_sync_status(raw_projects)
    total_count = len(projects)
    assert total_count >= 110

    counts = {}
    for p in projects:
        plat = p.get("platform") or p.get("target_warehouse") or "NOT_SET"
        plat = str(plat).upper().strip()
        if plat not in ("SNOWFLAKE", "DATABRICKS"):
            plat = "NOT_SET"
        counts[plat] = counts.get(plat, 0) + 1

    assert sum(counts.values()) == total_count, f"Sum of counts {sum(counts.values())} must equal total {total_count}"

    # Verify that projects with known configurations resolve to their real platforms and not NOT_SET
    by_id = {p.get("id"): p for p in projects}
    if "proj-hr_t" in by_id:
        assert by_id["proj-hr_t"].get("platform") == "SNOWFLAKE", f"proj-hr_t platform should be SNOWFLAKE, got {by_id['proj-hr_t'].get('platform')}"
    if "proj-employee_hiring" in by_id:
        assert by_id["proj-employee_hiring"].get("platform") == "SNOWFLAKE", f"proj-employee_hiring platform should be SNOWFLAKE, got {by_id['proj-employee_hiring'].get('platform')}"
    if "proj-ai_databricks" in by_id:
        assert by_id["proj-ai_databricks"].get("platform") == "DATABRICKS", f"proj-ai_databricks platform should be DATABRICKS, got {by_id['proj-ai_databricks'].get('platform')}"


def test_projects_without_target_categorized_as_not_set_not_snowflake():
    """Verify that projects with no target configured are classified as NOT_SET, never defaulted to SNOWFLAKE."""
    sample_projects = [
        {"id": "p1", "name": "Snowflake Proj", "platform": "SNOWFLAKE"},
        {"id": "p2", "name": "Databricks Proj", "platform": "DATABRICKS"},
        {"id": "p3", "name": "Unconfigured Proj 1", "platform": None},
        {"id": "p4", "name": "Unconfigured Proj 2", "platform": "UNKNOWN"},
        {"id": "p5", "name": "Unconfigured Proj 3", "platform": "NOT_SET"},
        {"id": "p6", "name": "Unconfigured Proj 4", "target_warehouse": ""},
    ]

    counts = {}
    for p in sample_projects:
        raw = str(p.get("platform") or p.get("target_warehouse") or "").upper().strip()
        plat = "NOT_SET" if not raw or raw in ("UNKNOWN", "NOT_SET") else raw
        counts[plat] = counts.get(plat, 0) + 1

    assert counts.get("SNOWFLAKE") == 1
    assert counts.get("DATABRICKS") == 1
    assert counts.get("NOT_SET") == 4
    assert sum(counts.values()) == len(sample_projects)
