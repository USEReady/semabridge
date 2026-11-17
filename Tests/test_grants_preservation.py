"""Grants preservation across view replacement.

Verifies Item 4: Re-apply grants after every replace (or COPY GRANTS); test before/after.
"""
from typing import List
from semabridge.core.project_resync import (
    backup_live_ddl_and_grants,
    reapply_grants_after_replace,
)


def test_grants_reapplied_after_view_replace(tmp_path):
    view_name = "DB.SCHEMA.TARGET_VIEW"
    before_grants = [
        "GRANT SELECT ON VIEW DB.SCHEMA.TARGET_VIEW TO ROLE ANALYST",
        "GRANT SELECT ON VIEW DB.SCHEMA.TARGET_VIEW TO ROLE BI_USER",
    ]
    original_ddl = "CREATE OR REPLACE VIEW DB.SCHEMA.TARGET_VIEW AS SELECT 1 AS id;"

    # 1. Back up live DDL and grants before deploy
    backup = backup_live_ddl_and_grants(
        project_id="proj-grants-test",
        run_id="run-1",
        view_names=[view_name],
        backup_dir=tmp_path,
        live_ddl_fetcher=lambda vn: original_ddl,
        live_grants_fetcher=lambda vn: list(before_grants),
    )
    assert view_name in backup["views"]
    assert backup["views"][view_name]["grants"] == before_grants

    # 2. Simulate replacing the view
    executed_sql: List[str] = []

    def mock_executor(sql: str):
        executed_sql.append(sql)

    new_ddl = "CREATE OR REPLACE VIEW DB.SCHEMA.TARGET_VIEW AS SELECT 2 AS id;"
    mock_executor(new_ddl)
    assert executed_sql == [new_ddl]

    # 3. Re-apply grants after replace
    captured_grants = backup["views"][view_name]["grants"]
    applied_count = reapply_grants_after_replace(view_name, captured_grants, executor=mock_executor)

    # 4. Verification: before/after parity
    assert applied_count == 2
    assert executed_sql == [
        new_ddl,
        "GRANT SELECT ON VIEW DB.SCHEMA.TARGET_VIEW TO ROLE ANALYST",
        "GRANT SELECT ON VIEW DB.SCHEMA.TARGET_VIEW TO ROLE BI_USER",
    ]


def test_reapply_grants_handles_empty_or_failing_gracefully():
    applied = reapply_grants_after_replace("V", [], executor=lambda s: None)
    assert applied == 0

    # Failing executor does not crash
    def failing_executor(s):
        raise RuntimeError("Snowflake permission denied")

    applied2 = reapply_grants_after_replace("V", ["GRANT SELECT ON VIEW V TO ROLE R"], executor=failing_executor)
    assert applied2 == 0
