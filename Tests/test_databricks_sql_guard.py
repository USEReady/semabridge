"""Tests for Databricks Unity Catalog SQL execution guard (Requirement 3)."""

from __future__ import annotations

import pytest

from semabridge.connectors.sql_guard import (
    DatabricksSQLGuardPolicy,
    SQLOwnershipSecurityError,
    SQLSecurityGuardError,
    validate_databricks_sql,
)


def _policy(**kwargs) -> DatabricksSQLGuardPolicy:
    base = dict(target_catalog="main", target_schema="analytics")
    base.update(kwargs)
    return DatabricksSQLGuardPolicy(**base)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM `main`.`analytics`.`mv_sales` LIMIT 0",
        "SELECT MEASURE(`total_revenue`) FROM `main`.`analytics`.`mv_sales__sbstg_abc12345` LIMIT 0",
        "WITH cte AS (SELECT 1 AS x) SELECT * FROM cte",
        "SHOW TABLES IN `main`.`analytics` LIKE 'mv_sales'",
        "SHOW SCHEMAS IN `main` LIKE 'analytics'",
        "SHOW GRANTS ON VIEW `main`.`analytics`.`mv_sales`",
        "DESCRIBE TABLE EXTENDED `main`.`analytics`.`mv_sales`",
        "DESCRIBE EXTENDED `main`.`analytics`.`mv_sales`",
        "DESC `main`.`analytics`.`mv_sales`",
    ],
)
def test_read_only_statements_allowed(sql: str):
    policy = _policy(read_only=True)
    validate_databricks_sql(sql, policy)


def test_read_only_policy_blocks_all_writes():
    policy = _policy(read_only=True)
    policy.register_view("`main`.`analytics`.`mv_sales`")
    with pytest.raises(SQLSecurityGuardError, match="read-only"):
        validate_databricks_sql(
            "CREATE OR REPLACE VIEW `main`.`analytics`.`mv_sales` AS SELECT 1",
            policy,
        )


def test_create_view_requires_registration_and_target_schema():
    policy = _policy()
    # Unregistered view -> blocked
    with pytest.raises(SQLOwnershipSecurityError, match="not registered"):
        validate_databricks_sql(
            "CREATE OR REPLACE VIEW `main`.`analytics`.`mv_sales` WITH METRICS LANGUAGE YAML AS $$\nversion: 1.1\n$$",
            policy,
        )

    # Registered view in target schema -> allowed
    policy.register_view("`main`.`analytics`.`mv_sales`")
    validate_databricks_sql(
        "CREATE OR REPLACE VIEW `main`.`analytics`.`mv_sales` WITH METRICS LANGUAGE YAML AS $$\nversion: 1.1\n$$",
        policy,
    )

    # Registered view in WRONG schema -> blocked
    policy.register_view("`main`.`other_schema`.`mv_sales`")
    with pytest.raises(SQLOwnershipSecurityError, match="outside target schema"):
        validate_databricks_sql(
            "CREATE OR REPLACE VIEW `main`.`other_schema`.`mv_sales` AS SELECT 1",
            policy,
        )


def test_drop_view_restricted_to_registered_drop_views():
    policy = _policy()
    policy.register_view("`main`.`analytics`.`mv_sales`")

    # Even if registered for CREATE, DROP VIEW must be explicitly registered
    with pytest.raises(SQLOwnershipSecurityError, match="only staging views or verified SemaBridge-owned views"):
        validate_databricks_sql("DROP VIEW IF EXISTS `main`.`analytics`.`mv_sales`", policy)

    # Staging or verified SemaBridge view -> allowed
    policy.register_drop_view("`main`.`analytics`.`mv_sales__sbstg_12345678`")
    validate_databricks_sql("DROP VIEW IF EXISTS `main`.`analytics`.`mv_sales__sbstg_12345678`", policy)


def test_grant_restricted_to_captured_grants():
    policy = _policy()
    # Uncaptured grant -> blocked
    with pytest.raises(SQLSecurityGuardError, match="only grants captured"):
        validate_databricks_sql(
            "GRANT SELECT ON VIEW `main`.`analytics`.`mv_sales` TO `analysts`",
            policy,
        )

    # Captured grant -> allowed
    policy.register_grant("SELECT", "`main`.`analytics`.`mv_sales`", "analysts")
    validate_databricks_sql(
        "GRANT SELECT ON VIEW `main`.`analytics`.`mv_sales` TO `analysts`",
        policy,
    )

    # Different principal or privilege -> still blocked
    with pytest.raises(SQLSecurityGuardError, match="only grants captured"):
        validate_databricks_sql(
            "GRANT ALL PRIVILEGES ON VIEW `main`.`analytics`.`mv_sales` TO `analysts`",
            policy,
        )
    with pytest.raises(SQLSecurityGuardError, match="only grants captured"):
        validate_databricks_sql(
            "GRANT SELECT ON VIEW `main`.`analytics`.`mv_sales` TO `other_group`",
            policy,
        )


def test_create_schema_requires_flag_and_exact_target():
    # Disabled by default -> blocked
    policy = _policy(allow_create_schema=False)
    with pytest.raises(SQLSecurityGuardError, match="create_schema_if_not_exists is disabled"):
        validate_databricks_sql("CREATE SCHEMA IF NOT EXISTS `main`.`analytics`", policy)

    # Enabled -> allowed for target schema only
    policy_on = _policy(allow_create_schema=True)
    validate_databricks_sql("CREATE SCHEMA IF NOT EXISTS `main`.`analytics`", policy_on)

    # Different schema -> still blocked
    with pytest.raises(SQLSecurityGuardError, match="only target schema"):
        validate_databricks_sql("CREATE SCHEMA IF NOT EXISTS `main`.`other`", policy_on)


def test_metadata_table_statements_strictly_scoped():
    # Disabled -> blocked
    policy_off = _policy(
        allow_metadata_table=False,
        metadata_table_fqn="`main`.`analytics`.`sales_semabridge_metadata`",
    )
    with pytest.raises(SQLSecurityGuardError, match="CREATE TABLE"):
        validate_databricks_sql(
            "CREATE OR REPLACE TABLE `main`.`analytics`.`sales_semabridge_metadata` (id INT) USING DELTA",
            policy_off,
        )

    # Enabled -> allowed ONLY for the _semabridge_metadata table
    policy_on = _policy(
        allow_metadata_table=True,
        metadata_table_fqn="`main`.`analytics`.`sales_semabridge_metadata`",
    )
    validate_databricks_sql(
        "CREATE OR REPLACE TABLE `main`.`analytics`.`sales_semabridge_metadata` (id INT) USING DELTA",
        policy_on,
    )
    validate_databricks_sql(
        "INSERT INTO `main`.`analytics`.`sales_semabridge_metadata` VALUES (1)",
        policy_on,
    )

    # Enabled, but targeting a non-metadata table -> blocked!
    with pytest.raises(SQLSecurityGuardError, match="CREATE TABLE"):
        validate_databricks_sql(
            "CREATE TABLE IF NOT EXISTS `main`.`analytics`.`real_fact_table` (id INT) USING DELTA",
            policy_on,
        )
    with pytest.raises(SQLSecurityGuardError, match="INSERT"):
        validate_databricks_sql(
            "INSERT INTO `main`.`analytics`.`real_fact_table` VALUES (1)",
            policy_on,
        )

    # Even if someone misconfigures metadata_table_fqn without _semabridge_metadata -> blocked!
    bad_policy = _policy(
        allow_metadata_table=True,
        metadata_table_fqn="`main`.`analytics`.`sales`",
    )
    with pytest.raises(SQLSecurityGuardError, match="CREATE TABLE"):
        validate_databricks_sql(
            "CREATE OR REPLACE TABLE `main`.`analytics`.`sales` (id INT) USING DELTA",
            bad_policy,
        )


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("DROP TABLE IF EXISTS `main`.`analytics`.`sales`", "DROP TABLE"),
        ("CREATE TABLE IF NOT EXISTS `main`.`analytics`.`sales` (id INT) USING DELTA", "CREATE TABLE"),
        ("INSERT INTO `main`.`analytics`.`sales` VALUES (1)", "INSERT"),
        ("UPDATE `main`.`analytics`.`sales` SET x = 1", "UPDATE"),
        ("DELETE FROM `main`.`analytics`.`sales` WHERE id = 1", "DELETE"),
        ("MERGE INTO `main`.`analytics`.`sales` USING src ON 1=1 WHEN MATCHED THEN DELETE", "MERGE"),
        ("ALTER TABLE `main`.`analytics`.`sales` ADD COLUMNS (k STRING)", "ALTER TABLE"),
        ("TRUNCATE TABLE `main`.`analytics`.`sales`", "TRUNCATE"),
        ("COPY INTO `main`.`analytics`.`sales` FROM '/tmp/x'", "COPY INTO"),
        ("CALL some_proc()", "CALL"),
        ("DROP SCHEMA `main`.`analytics`", "DROP CATALOG/DATABASE/SCHEMA"),
        ("REVOKE SELECT ON VIEW `main`.`analytics`.`mv_sales` FROM `analysts`", "REVOKE"),
        ("SELECT 1; DROP TABLE `main`.`analytics`.`sales`", "DROP TABLE"),
    ],
)
def test_forbidden_statements_are_refused(sql: str, expected: str):
    policy = _policy()
    with pytest.raises(SQLSecurityGuardError, match=expected):
        validate_databricks_sql(sql, policy)
