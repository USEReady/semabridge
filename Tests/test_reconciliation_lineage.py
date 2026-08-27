"""
Unit tests for lineage-aware metric reconciliation.
Verifies that auto-generated branch metrics (e.g. KPI01_BRANCH_1) whose parent (KPI01)
was dropped or recorded in DropLedger are correctly accounted for and do not trigger false-positive silent loss warnings.
"""

import pytest
from semabridge.core.reconciliation import compute_reconciliation
from semabridge.utils.identifiers import IdentifierSanitizer


def test_reconciliation_parent_drop_accounts_for_branch_metrics():
    """
    Test that when a parent metric 'KPI01' is dropped during deployment,
    its companion branch metrics ('KPI01_BRANCH_1', 'KPI01_BRANCH_2') in the snapshot
    are recognized as accounted for via parent lineage and do not appear in unaccounted.
    """
    snapshot_metric_names = [
        "KPI01",
        "KPI01_BRANCH_1",
        "KPI01_BRANCH_2",
        "SALES_TOTAL",
    ]

    # Deployed DDL only contains SALES_TOTAL
    deployed_ddl_text = """
    CREATE SEMANTIC VIEW SALES_MODEL AS
    TABLES (
      FACT_SALES AS PUBLIC.FACT_SALES
    )
    METRICS (
      FACT_SALES.SALES_TOTAL AS SUM(FACT_SALES.AMOUNT)
    );
    """

    # Drop records contains parent metric KPI01 dropped during ddl_deployment
    drop_records = [
        {
            "entity_kind": "metric",
            "entity_name": "KPI01",
            "stage": "ddl_deployment",
            "reason": "Snowflake rejected this identifier when executing the compiled semantic-view DDL.",
            "detail": "SQL compilation error: invalid identifier 'ATINDICATOR02.VALUE'",
        }
    ]

    report = compute_reconciliation(
        run_id="test-run-123",
        project_id="test-proj-456",
        snapshot_id="test-snap-789",
        snapshot_metric_names=snapshot_metric_names,
        deployed_ddl_text=deployed_ddl_text,
        drop_records=drop_records,
        sanitizer=IdentifierSanitizer(),
    )

    # Invariant: KPI01_BRANCH_1 and KPI01_BRANCH_2 should NOT appear in unaccounted!
    assert "KPI01_BRANCH_1" not in report.unaccounted
    assert "KPI01_BRANCH_2" not in report.unaccounted
    assert report.is_clean(), f"Report should be clean, but got unaccounted: {report.unaccounted}"
