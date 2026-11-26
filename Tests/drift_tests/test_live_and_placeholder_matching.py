"""Tests for real-scale view comparison, qualification matching, and placeholder suppression.

Verifies:
1. A view deployed and checked immediately produces zero findings on 100+ metrics.
2. Baseline and live with different table qualification or case for the same object match as one object.
3. Placeholder expressions (e.g. CAST(NULL AS DOUBLE)) and never-deployed objects never produce findings.
4. Regression test using real Snowflake Semantic View DDL syntax with PRIVATE visibility,
   NON ADDITIVE BY clauses, and WITH SYNONYMS clauses.
"""

import pytest
from semabridge.governance.drift.models import (
    ViewDefinition,
    ObjectDef,
    ObjectKind,
    DriftCategory,
)
from semabridge.governance.drift.comparison import compare_views
from semabridge.governance.drift.adapters.snowflake import SnowflakeAdapter


def test_view_deployed_and_checked_immediately_zero_findings_hundred_metrics():
    """A view deployed and checked with 120 metrics produces exactly 0 drift findings."""
    base_objs = []
    live_objs = []
    for i in range(120):
        name = f"METRIC_{i}"
        expr = f"SUM(T.COL_{i}::FLOAT) / 100" if i % 5 != 0 else "CAST(NULL AS DOUBLE)"
        base_objs.append(ObjectDef(kind=ObjectKind.METRIC, table="CORE_VIEW", name=name, expression=expr))
        live_objs.append(ObjectDef(kind=ObjectKind.METRIC, table="T", name=name, expression=expr))

    base_def = ViewDefinition(
        view_name="DB.SCH.CORE_VIEW",
        platform="SNOWFLAKE",
        source="baseline",
        retrieved_at="2026-11-24T12:00:00Z",
        objects=base_objs,
        capabilities={"METRIC": {"expression", "description"}},
    )
    live_def = ViewDefinition(
        view_name="DB.SCH.CORE_VIEW",
        platform="SNOWFLAKE",
        source="live",
        retrieved_at="2026-11-24T12:00:00Z",
        objects=live_objs,
        capabilities={"METRIC": {"expression", "description"}},
    )

    findings = compare_views(base_def, live_def)
    assert len(findings) == 0, f"Expected 0 findings, got {len(findings)}: {findings}"


def test_different_qualification_or_casing_matches_as_one_object():
    """Baseline scoped to view and live scoped to table alias match as one object."""
    base_def = ViewDefinition(
        view_name="DB.SCH.SALES_VIEW",
        platform="SNOWFLAKE",
        source="baseline",
        retrieved_at="2026-11-24T12:00:00Z",
        objects=[
            ObjectDef(kind=ObjectKind.METRIC, table="SALES_VIEW", name="TOTAL_REVENUE", expression="SUM(SALES.AMOUNT)"),
            ObjectDef(kind=ObjectKind.DIMENSION, table="SALES_VIEW", name="REGION_CODE", expression="SALES.REGION"),
        ],
        capabilities={"METRIC": {"expression"}, "DIMENSION": {"expression"}},
    )
    live_def = ViewDefinition(
        view_name="DB.SCH.SALES_VIEW",
        platform="SNOWFLAKE",
        source="live",
        retrieved_at="2026-11-24T12:00:00Z",
        objects=[
            ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="total_revenue", expression="sum(sales.amount)"),
            ObjectDef(kind=ObjectKind.DIMENSION, table="SALES", name="region_code", expression="sales.region"),
        ],
        capabilities={"METRIC": {"expression"}, "DIMENSION": {"expression"}},
    )

    findings = compare_views(base_def, live_def)
    assert len(findings) == 0, f"Expected 0 findings for qualified/cased match, got: {findings}"


def test_placeholder_and_never_deployed_objects_never_produce_findings():
    """Placeholders and objects marked not-deployed never emit deletion drift."""
    base_def = ViewDefinition(
        view_name="DB.SCH.REMED_VIEW",
        platform="SNOWFLAKE",
        source="baseline",
        retrieved_at="2026-11-24T12:00:00Z",
        objects=[
            # Placeholder in baseline that has no match on live
            ObjectDef(kind=ObjectKind.METRIC, table="REMED_VIEW", name="BROKEN_MEASURE", expression="CAST(NULL AS DOUBLE)"),
            # Another placeholder format
            ObjectDef(kind=ObjectKind.METRIC, table="REMED_VIEW", name="EMPTY_MEASURE", expression="CAST(NULL AS VARCHAR)"),
            # Dropped metric in not_deployed_objects
            ObjectDef(kind=ObjectKind.METRIC, table="REMED_VIEW", name="UNSUPPORTED_WINDOW_METRIC", expression="AVG(X) OVER ()"),
            # Valid metric on both sides
            ObjectDef(kind=ObjectKind.METRIC, table="REMED_VIEW", name="ACTIVE_METRIC", expression="COUNT(*)"),
        ],
        not_deployed_objects=["UNSUPPORTED_WINDOW_METRIC"],
        capabilities={"METRIC": {"expression"}},
    )
    live_def = ViewDefinition(
        view_name="DB.SCH.REMED_VIEW",
        platform="SNOWFLAKE",
        source="live",
        retrieved_at="2026-11-24T12:00:00Z",
        objects=[
            ObjectDef(kind=ObjectKind.METRIC, table="T", name="ACTIVE_METRIC", expression="COUNT(*)"),
        ],
        capabilities={"METRIC": {"expression"}},
    )

    findings = compare_views(base_def, live_def)
    assert len(findings) == 0, f"Expected 0 findings (placeholders and dropped must be suppressed), got: {findings}"


def test_real_snowflake_semantic_view_ddl_syntax_parsing():
    """Verify Snowflake DDL parser handles PRIVATE visibility, NON ADDITIVE BY, and SYNONYMS."""
    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW "SEMABRIDGE"."TEST"."V_SAMPLE"
    TABLES (
        "SEMABRIDGE"."TEST"."INDICATORS" AS INDICATORS,
        SEMABRIDGE.TEST.YEARS primary key (YEARS)
    )
    RELATIONSHIPS (
        INDICATORS_YEARS_FK as INDICATORS(YEAR) references YEARS(YEAR)
    )
    DIMENSIONS (
        INDICATORS.COUNTRY_CODE as INDICATORS."COUNTRY_CODE" with synonyms=('country','nation'),
        YEARS.YEAR as YEARS."YEAR"
    )
    METRICS (
        PRIVATE INDICATORS.GDP_CNT as COUNT(INDICATORS.GDP),
        PRIVATE INDICATORS.GDP_SUM as SUM(INDICATORS.GDP::FLOAT),
        INDICATORS.CLE non additive by (YEARS.YEAR asc nulls last) as COUNT(DISTINCT INDICATORS.COUNTRY_CODE) with synonyms=('cle','country count'),
        INDICATORS.BROKEN_MEASURE as CAST(NULL AS DOUBLE) comment='not applicable: column missing',
        FLAGS.GOAL as MAX(100) with synonyms=('goal','target')
    );
    """
    adapter = SnowflakeAdapter()
    parsed = adapter.parse_definition(ddl, view_name="V_SAMPLE")
    objs = {o.name: o for o in parsed.objects}

    # Verify metrics
    assert "GDP_CNT" in objs
    assert objs["GDP_CNT"].kind == ObjectKind.METRIC
    assert objs["GDP_CNT"].table == "INDICATORS"
    assert objs["GDP_CNT"].expression == "COUNT(INDICATORS.GDP)"

    assert "CLE" in objs
    assert objs["CLE"].kind == ObjectKind.METRIC
    assert objs["CLE"].table == "INDICATORS"
    assert objs["CLE"].expression == "COUNT(DISTINCT INDICATORS.COUNTRY_CODE)"
    assert objs["CLE"].properties.get("synonyms") == "'cle','country count'"

    assert "BROKEN_MEASURE" in objs
    assert objs["BROKEN_MEASURE"].expression == "CAST(NULL AS DOUBLE)"
    assert objs["BROKEN_MEASURE"].description == "not applicable: column missing"

    assert "GOAL" in objs
    assert objs["GOAL"].table == "FLAGS"
    assert objs["GOAL"].expression == "MAX(100)"

    # Verify dimensions
    assert "COUNTRY_CODE" in objs
    assert objs["COUNTRY_CODE"].kind == ObjectKind.DIMENSION
    assert objs["COUNTRY_CODE"].table == "INDICATORS"

    # Verify facts
    assert "INDICATORS" in objs
    assert objs["INDICATORS"].kind == ObjectKind.FACT

    assert "YEARS" in objs
    assert objs["YEARS"].kind == ObjectKind.FACT

    # Verify relationships
    assert "INDICATORS_YEARS_FK" in objs
    assert objs["INDICATORS_YEARS_FK"].kind == ObjectKind.RELATIONSHIP


def test_bulk_resolve_and_paged_drift_events_filtering(monkeypatch):
    """Verify bulk resolution and server-side filtering, sorting, and pagination."""
    from semabridge.governance.drift_service import (
        bulk_resolve_drift_events,
        clear_drift_events,
        _load_state,
        _save_state,
    )
    from semabridge.api.routers.drift_router import get_drift_events
    from semabridge.repository.model_repository import ModelRepository

    monkeypatch.setattr(ModelRepository, "validate_project_view", lambda self, pid, v: v)

    # Prepare isolated test state
    clear_drift_events(project_id="bulk-test-proj")
    state = _load_state()
    events = []
    # Create 35 events: 20 deletions, 10 additions, 5 expression drift
    for i in range(35):
        if i < 20:
            cat = "DELETION"
            kind = "METRIC"
        elif i < 30:
            cat = "ADDITION"
            kind = "DIMENSION"
        else:
            cat = "EXPRESSION_DRIFT"
            kind = "METRIC"

        events.append({
            "id": f"drift-bulk-{i:03d}",
            "project_id": "bulk-test-proj",
            "semantic_view": "DB.SCH.BULK_VIEW",
            "metric_name": f"OBJ_{i:03d}",
            "object_name": f"OBJ_{i:03d}",
            "kind": kind,
            "category": cat,
            "drift_type": cat,
            "status": "pending",
            "detected_at": f"2026-11-24T10:{i:02d}:00Z",
            "last_seen_at": f"2026-11-24T10:{i:02d}:00Z",
            "expression": f"SUM(COL_{i})",
            "mode": "live",
        })

    state["events"] = [e for e in state.get("events") or [] if e.get("project_id") != "bulk-test-proj"] + events
    _save_state(state)

    # 1. Test get_drift_events default (all 35 events, aggregate counts agreed)
    res = get_drift_events(project_id="bulk-test-proj", view="DB.SCH.BULK_VIEW", mode="live", page=1, page_size=10)
    assert res["total_count"] == 35
    assert res["total_unfiltered_count"] == 35
    assert res["count"] == 10
    assert res["total_pages"] == 4
    assert res["counts_by_category"]["DELETION"] == 20
    assert res["counts_by_category"]["ADDITION"] == 10
    assert res["counts_by_category"]["EXPRESSION_DRIFT"] == 5
    assert res["counts_by_kind"]["METRIC"] == 25
    assert res["counts_by_kind"]["DIMENSION"] == 10
    assert res["counts_by_status"]["pending"] == 35

    # 2. Test filtering by category
    del_res = get_drift_events(project_id="bulk-test-proj", view="DB.SCH.BULK_VIEW", category="DELETION", mode="live", page=1, page_size=10)
    assert del_res["total_count"] == 20
    assert del_res["total_unfiltered_count"] == 35  # Unfiltered total still reports all 35
    assert del_res["counts_by_category"]["DELETION"] == 20
    assert all(e["category"] == "DELETION" for e in del_res["events"])

    # 3. Test searching by name
    search_res = get_drift_events(project_id="bulk-test-proj", view="DB.SCH.BULK_VIEW", search="OBJ_005", mode="live")
    assert search_res["total_count"] == 1
    assert search_res["events"][0]["object_name"] == "OBJ_005"

    # 4. Test bulk resolution: Ignore all Deletions in bulk-test-proj
    bulk_res = bulk_resolve_drift_events(
        project_id="bulk-test-proj",
        view="DB.SCH.BULK_VIEW",
        category="DELETION",
        action="ignore",
        reason="Approved deletion cleanup",
    )
    assert bulk_res["success"] is True
    assert bulk_res["resolved_count"] == 20

    # 5. Verify status updated in get_drift_events
    after_res = get_drift_events(project_id="bulk-test-proj", view="DB.SCH.BULK_VIEW", mode="live")
    assert after_res["counts_by_status"]["ignored"] == 20
    assert after_res["counts_by_status"]["pending"] == 15

    # Clean up
    clear_drift_events(project_id="bulk-test-proj")

