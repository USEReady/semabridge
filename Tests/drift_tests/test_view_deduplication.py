"""Tests verifying view deduplication across name forms and DAX expression normalization.

Verifies:
1. Two deployment records / domain entries under different name forms (e.g. bare vs qualified)
   produce exactly one checked view and one object count during drift checks.
2. ModelRepository.list_project_deployments deduplicates multiple deployments of the same canonical view,
   preserving database, schema, and fully-qualified semantic_view.
3. DAX expressions are normalized with dedicated DAX normalizer (dialect="dax") yielding method="parsed"
   without sqlglot syntax errors.
4. Repeated sqlglot parse errors emit at most one summary warning per check run.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from semabridge.governance.drift import save_deploy_baseline
from semabridge.governance.drift.normalize import (
    normalize_expression,
    normalize_dax_expression,
    reset_parse_warnings,
)
from semabridge.governance.drift_service import (
    run_drift_check,
    clear_drift_events,
)
from semabridge.repository.model_repository import ModelRepository


@pytest.fixture(autouse=True)
def clean_state():
    clear_drift_events(_allow_all_for_testing=True)
    reset_parse_warnings()
    yield


def test_two_name_forms_produce_one_checked_view_and_one_object_count():
    """Verify that multiple name forms of the same view produce 1 checked view and 1 object count."""
    project_id = "test-proj-dedup-views"
    platform = "SNOWFLAKE"
    bare_name = "SALES_SUMMARY_VIEW"
    qualified_name = '"DB_PROD"."SCHEMA_ANALYTICS"."SALES_SUMMARY_VIEW"'

    ddl = (
        'CREATE OR REPLACE SEMANTIC VIEW "DB_PROD"."SCHEMA_ANALYTICS"."SALES_SUMMARY_VIEW"\n'
        'TABLES ( FACT_SALES AS "DB_PROD"."SCHEMA_ANALYTICS"."FACT_SALES" )\n'
        'METRICS (\n'
        '  TOTAL_REVENUE AS SUM(AMOUNT) COMMENT \'Total transaction revenue\'\n'
        ')'
    )

    # Save baseline under qualified name
    save_deploy_baseline(
        project_id=project_id,
        platform=platform,
        view_name=qualified_name,
        definition_text=ddl,
        metrics={"TOTAL_REVENUE": "SUM(AMOUNT)"},
        descriptions={"TOTAL_REVENUE": "Total transaction revenue"},
    )

    # Simulate two domains with different name forms of the SAME canonical view
    dom1 = MagicMock()
    dom1.domain = "Domain 1"
    dom1.semantic_view_name = qualified_name
    dom1.source_project_id = project_id
    m1 = MagicMock()
    m1.name = "TOTAL_REVENUE"
    m1.sql_expression = "SUM(AMOUNT)"
    m1.dax_expression = "SUM(FactSales[AMOUNT])"
    dom1.metrics = [m1]

    dom2 = MagicMock()
    dom2.domain = "Domain 2"
    dom2.semantic_view_name = bare_name
    dom2.source_project_id = project_id
    m2 = MagicMock()
    m2.name = "TOTAL_REVENUE"
    m2.sql_expression = "SUM(AMOUNT)"
    m2.dax_expression = "SUM(FactSales[AMOUNT])"
    dom2.metrics = [m2]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect"), \
         patch("semabridge.governance.drift.adapters.snowflake.SnowflakeAdapter.fetch_definition_text", return_value=ddl), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[dom1, dom2]):

        # Run drift check without view parameter (evaluates all project domains)
        res = run_drift_check(project_id=project_id, view=None)

        assert res["status"] in ("IN_SYNC", "COMPLETED")
        # Must check exactly 1 view, NOT 2
        assert len(res["views_checked"]) == 1
        assert res["views_checked"][0] == qualified_name
        # Exactly 2 objects verified (1 table + 1 metric), NOT double-counted to 4
        assert res["checked_metrics"] == 2
        assert res["drift_detected"] == 0


def test_dax_expression_normalizer():
    """Verify DAX normalization handles comments, table quotes, leading equals, and casing."""
    dax_1 = "= SUM('Indicators'[Life expectancy (yrs)]) // current life expectancy"
    dax_2 = "sum(Indicators[Life expectancy (yrs)])"
    dax_3 = "/* formula */ SUM ( 'Indicators' [Life expectancy (yrs)] )"

    n1 = normalize_expression(dax_1, dialect="dax")
    n2 = normalize_expression(dax_2, dialect="dax")
    n3 = normalize_expression(dax_3, dialect="dax")

    assert n1.method == "parsed"
    assert n2.method == "parsed"
    assert n3.method == "parsed"
    assert str(n1) == str(n2) == str(n3)
    assert str(n1) == "SUM(INDICATORS[LIFE EXPECTANCY (YRS)])"

    # Preserves double-quoted string literals exactly
    dax_str1 = 'IF([Status] = "Active", 1, 0)'
    dax_str2 = 'if([status] = "Active", 1, 0)'
    ns1 = normalize_expression(dax_str1, dialect="dax")
    ns2 = normalize_expression(dax_str2, dialect="dax")
    assert str(ns1) == str(ns2)
    assert '"Active"' in str(ns1)


def test_sqlglot_parse_failure_warning_suppression(caplog):
    """Verify that multiple parse failures emit at most one summary warning per check run."""
    reset_parse_warnings()
    bad_expr = "INVALID SYNTAX %%% NOT SQL ((("

    with caplog.at_level(logging.WARNING, logger="semabridge.governance.drift.normalize"):
        for _ in range(5):
            normalize_expression(bad_expr, dialect="snowflake")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "falling back to text cleanup" in warnings[0].message


def test_list_project_deployments_deduplicates_different_name_forms():
    """Verify that ModelRepository.list_project_deployments deduplicates two records of same view."""
    repo = ModelRepository()
    proj_id = "test-proj-deploy-dedup"

    # Simulate raw deployment rows: one bare from run_model_results, one qualified from SnapshotRow
    snap_row = MagicMock()
    snap_row.view_name = "METRIC_VIEW_SEMANTIC"
    snap_row.target_database = "PROD_DB"
    snap_row.target_schema = "REPORTING"
    snap_row.deployed_at = "2026-11-26T10:00:00Z"
    snap_row.timestamp = "2026-11-26T10:00:00Z"
    snap_row.run_id = "run-1"
    snap_row.snapshot_id = "snap-1"
    snap_row.sml_blob = None

    # Mock session query results
    with patch.object(repo, "_session") as mock_session_ctx:
        session = MagicMock()
        mock_session_ctx.return_value.__enter__.return_value = session

        # project query
        session.execute.return_value.first.return_value = ("PROD_DB", "REPORTING", "snowflake")

        # Snapshot rows query and run_model_results query
        exec_results = [
            # First execute: Project query (proj_row)
            MagicMock(first=lambda: ("PROD_DB", "REPORTING", "snowflake")),
            # Second execute: SnapshotRow query
            MagicMock(all=lambda: [(snap_row, "PROD_DB", "REPORTING", "snowflake", "snowflake", "DEPLOY")]),
            # Third execute: run_model_results query (has bare view_name and empty namespace)
            MagicMock(fetchall=lambda: [("success", "METRIC_VIEW_SEMANTIC", "", "run-2", None)]),
        ]
        session.execute.side_effect = exec_results

        deployments = repo.list_project_deployments(proj_id, include_unmanaged=False)

        # Must return exactly 1 deployment for METRIC_VIEW_SEMANTIC
        assert len(deployments) == 1
        d = deployments[0]
        assert d["view"] == "METRIC_VIEW_SEMANTIC"
        assert d["database"] == "PROD_DB"
        assert d["schema"] == "REPORTING"
        assert d["semantic_view"] == 'PROD_DB.REPORTING.METRIC_VIEW_SEMANTIC'
        assert d["is_latest"] is True
