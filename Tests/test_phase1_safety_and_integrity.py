"""Unit tests for Phase 1: SQL Execution Safety Guard, Ownership Rules, and Data Integrity Checker."""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from semabridge.connectors.sql_guard import (
    validate_safe_sql,
    SQLSecurityGuardError,
    SQLOwnershipSecurityError,
    split_statements,
    strip_sql_comments,
    set_allowed_model_objects,
    clear_allowed_model_objects,
    check_view_ownership,
    SEMABRIDGE_SIGNATURE_COMMENT,
)
from semabridge.connectors.data_integrity_checker import (
    verify_data_integrity,
    check_table_integrity,
    DataIntegrityDiscrepancyError,
)


class TestSQLSecurityGuard:
    """Tests that default-deny allowlist strictly enforces permissions and blocks all forbidden SQL."""

    @pytest.mark.parametrize(
        "allowed_sql",
        [
            "SELECT * FROM \"SALES_FACT\"",
            "WITH cte AS (SELECT 1 AS val) SELECT * FROM cte",
            "SHOW TABLES LIKE 'PRODUCT%'",
            "SHOW SEMANTIC VIEWS",
            "SHOW VIEWS IN SCHEMA SEMABRIDGE_WORKSPACE",
            "SHOW COLUMNS IN TABLE \"GEO\"",
            "DESCRIBE TABLE \"DATE\"",
            "DESC VIEW \"V\"",
            "USE ROLE ACCOUNTADMIN",
            "USE WAREHOUSE COMPUTE_WH",
            "USE DATABASE SEMABRIDGE",
            "USE SCHEMA SEMABRIDGE_WORKSPACE",
            "ALTER SESSION SET TIMEZONE = 'UTC'",
            """CREATE OR REPLACE SEMANTIC VIEW "SEMABRIDGE"."SEMABRIDGE_WORKSPACE"."SALES_SV"
               TABLES (
                 SALES_FACT AS "SEMABRIDGE"."SEMABRIDGE_WORKSPACE"."SALES_FACT"
               )
               DIMENSIONS (
                 SALES_FACT.DATE AS "DATE"
               )
               METRICS (
                 TOTAL_UNITS AS SUM(SALES_FACT.UNITS)
               )""",
            "DROP SEMANTIC VIEW IF EXISTS \"SALES_SV\"",
            "ALTER SEMANTIC VIEW \"SALES_SV\" SET COMMENT = 'Updated documentation'",
            "ALTER SEMANTIC VIEW \"SALES_SV\" UNSET COMMENT",
            "CREATE OR REPLACE VIEW \"SALES_FACT_ENRICHED\" AS SELECT * FROM \"SALES_FACT\"",
            "DROP VIEW IF EXISTS \"SALES_FACT_ENRICHED\"",
            "-- Just a comment\nSELECT 1;",
            "/* Multi-line\ncomment */ SHOW TABLES;",
            "   \t\n  SELECT 1  \n\t",
        ],
    )
    def test_allowed_statements_pass(self, allowed_sql: str):
        set_allowed_model_objects({"SALES_SV", "SALES_FACT_ENRICHED"})
        try:
            validate_safe_sql(allowed_sql)
        finally:
            clear_allowed_model_objects()

    @pytest.mark.parametrize(
        "forbidden_sql, op_name",
        [
            ("EXECUTE IMMEDIATE 'DROP TABLE SALES_FACT'", "EXECUTE IMMEDIATE"),
            ("EXECUTE IMMEDIATE $$ ALTER TABLE T DROP COLUMN C $$", "EXECUTE IMMEDIATE"),
            ("CALL my_stored_procedure()", "CALL"),
            ("call cleanup_tables(1, 2)", "CALL"),
            ("CREATE TABLE \"CLONED\" CLONE \"SALES_FACT\"", "CLONE"),
            ("CREATE TABLE \"NEW_TBL\" (id INT)", "CREATE TABLE"),
            ("CREATE OR REPLACE TABLE \"NEW_TBL\" (id INT)", "CREATE TABLE"),
            ("CREATE TEMPORARY TABLE \"TEMP_TBL\" (id INT)", "CREATE TABLE"),
            ("create temporary table temp_tbl (id int)", "CREATE TABLE"),
            ("CREATE TRANSIENT TABLE \"TRANSIENT_TBL\" (id INT)", "CREATE TABLE"),
            ("create transient table trans_tbl (id int)", "CREATE TABLE"),
            ("CREATE TABLE \"BACKUP\" AS SELECT * FROM \"SALES_FACT\"", "CREATE TABLE"),
            ("ALTER TABLE \"PRODUCT\" SWAP WITH \"PRODUCT__FIXED\"", "ALTER TABLE"),
            ("ALTER TABLE \"SALES_FACT\" ADD COLUMN \"EXTRA\" VARCHAR", "ALTER TABLE"),
            ("ALTER TABLE \"DATE\" DROP COLUMN \"YEAR\"", "ALTER TABLE"),
            ("ALTER TABLE \"SALES_FACT\" RENAME TO \"SALES_FACT_OLD\"", "ALTER TABLE"),
            ("alter table date rename to old_date", "ALTER TABLE"),
            ("DROP TABLE \"SALES_FACT\"", "DROP TABLE"),
            ("DROP TABLE IF EXISTS \"DATE\"", "DROP TABLE"),
            ("DROP SCHEMA \"SEMABRIDGE_WORKSPACE\"", "DROP DATABASE/SCHEMA"),
            ("DROP DATABASE \"SEMABRIDGE\"", "DROP DATABASE/SCHEMA"),
            ("drop database if exists production_db", "DROP DATABASE/SCHEMA"),
            ("GRANT SELECT ON TABLE \"SALES_FACT\" TO ROLE PUBLIC", "GRANT/REVOKE"),
            ("REVOKE ALL PRIVILEGES ON TABLE \"DATE\" FROM ROLE ANALYST", "GRANT/REVOKE"),
            ("CREATE STAGE \"SEMABRIDGE_STAGE\"", "CREATE STAGE"),
            ("create or replace stage my_stage comment = 'store'", "CREATE STAGE"),
            ("TRUNCATE TABLE \"SALES_FACT\"", "TRUNCATE TABLE"),
            ("TRUNCATE \"GEO\"", "TRUNCATE TABLE"),
            ("INSERT INTO \"SALES_FACT\" VALUES (1, 2, 3)", "INSERT"),
            ("INSERT INTO \"PRODUCT\" (PRODUCT_ID) VALUES (999)", "INSERT"),
            ("UPDATE \"SALES_FACT\" SET UNITS = 0", "UPDATE"),
            ("UPDATE \"PRODUCT\" t SET t.SEGMENT = 'A'", "UPDATE"),
            ("DELETE FROM \"SALES_FACT\" WHERE UNITS IS NULL", "DELETE"),
            ("DELETE FROM \"DATE\"", "DELETE"),
            ("COPY INTO \"SALES_FACT\" FROM @my_stage/data.csv", "COPY INTO"),
            ("PUT file:///tmp/data.csv @my_stage", "PUT"),
            ("GET @my_stage file:///tmp/downloads", "GET"),
            ("MERGE INTO \"SALES_FACT\" USING \"NEW_FACT\" ON 1=1 WHEN MATCHED THEN UPDATE SET UNITS=1", "MERGE INTO"),
            ("SELECT 1; DROP TABLE \"SALES_FACT\";", "DROP TABLE"),
            ("SELECT 1; CREATE TABLE evil(id INT); SELECT 2", "CREATE TABLE"),
            ("/* Safe-looking prefix */ ALTER TABLE \"DATE\" SWAP WITH \"OLD_DATE\"", "ALTER TABLE"),
            ("-- comment\n cReAtE   tAbLe   test (x int)", "CREATE TABLE"),
            ("   \t\n  DROP TABLE \"PRODUCT\"  \n\t", "DROP TABLE"),
        ],
    )
    def test_forbidden_statements_raise_error(self, forbidden_sql: str, op_name: str):
        clear_allowed_model_objects()
        with pytest.raises(SQLSecurityGuardError) as exc_info:
            validate_safe_sql(forbidden_sql)
        assert "blocked" in str(exc_info.value).lower()

    def test_split_statements_respects_semicolons_inside_quotes(self):
        sql = "ALTER SEMANTIC VIEW \"V\" SET COMMENT = 'First; Second; Third'; SELECT 1"
        stmts = split_statements(sql)
        assert len(stmts) == 2
        assert "SET COMMENT" in stmts[0]
        assert "SELECT 1" in stmts[1]


class TestOwnershipRule:
    """Tests the ownership rule for creating, replacing, and dropping views and semantic views."""

    def test_allowed_model_objects_permit_registered_views(self):
        set_allowed_model_objects({"SALES_FACT_ENRICHED", "COMPETITIVE_MARKETING_SV"})
        # Registered view passes
        validate_safe_sql('CREATE OR REPLACE VIEW "SALES_FACT_ENRICHED" AS SELECT 1')
        validate_safe_sql('DROP VIEW IF EXISTS "SALES_FACT_ENRICHED"')
        clear_allowed_model_objects()

    def test_unregistered_object_rejected_under_ownership_rule(self):
        set_allowed_model_objects({"SALES_FACT_ENRICHED"})
        # Foreign view not in registered set is rejected
        with pytest.raises(SQLOwnershipSecurityError) as exc_info:
            validate_safe_sql('CREATE OR REPLACE VIEW "CUSTOMER_PORTAL_VIEW" AS SELECT 1')
        assert "ownership rule violation" in str(exc_info.value).lower()
        clear_allowed_model_objects()

    def test_empty_allowed_set_fails_closed(self):
        clear_allowed_model_objects()
        with pytest.raises(SQLOwnershipSecurityError) as exc_info:
            validate_safe_sql('CREATE OR REPLACE VIEW "SALES_FACT_ENRICHED" AS SELECT 1')
        assert "empty allowed set" in str(exc_info.value).lower()

        with pytest.raises(SQLOwnershipSecurityError) as exc_info:
            validate_safe_sql('CREATE OR REPLACE SEMANTIC VIEW "MY_SV" TABLES (t1 AS SELECT 1)')
        assert "empty allowed set" in str(exc_info.value).lower()

    def test_existing_unmarked_view_with_semabridge_like_name_is_refused(self):
        # A view with a SemaBridge-like name is registered in allowed_model_objects,
        # but in Snowflake it exists WITHOUT the "Created by SemaBridge" signature comment.
        set_allowed_model_objects({"SALES_FACT_ENRICHED", "COMPETITIVE_MARKETING_SV"})
        mock_cursor = MagicMock()
        mock_cursor.description = [("name",), ("schema_name",), ("comment",)]
        # Snowflake returns an existing view with an empty / unmarked comment
        mock_cursor.fetchall.return_value = [
            ("SALES_FACT_ENRICHED", "SEMABRIDGE_WORKSPACE", ""),
        ]

        with pytest.raises(SQLOwnershipSecurityError) as exc_info:
            validate_safe_sql(
                'CREATE OR REPLACE VIEW "SALES_FACT_ENRICHED" AS SELECT 1',
                cursor=mock_cursor,
                check_ownership=True,
            )
        assert "not created by semabridge" in str(exc_info.value).lower()

        # Also test semantic view without signature
        mock_cursor.fetchall.return_value = [
            ("COMPETITIVE_MARKETING_SV", "SEMABRIDGE_WORKSPACE", "Manual unmanaged view"),
        ]
        with pytest.raises(SQLOwnershipSecurityError) as exc_info:
            validate_safe_sql(
                'CREATE OR REPLACE SEMANTIC VIEW "COMPETITIVE_MARKETING_SV" TABLES (t1 AS SELECT 1)',
                cursor=mock_cursor,
                check_ownership=True,
            )
        assert "not created by semabridge" in str(exc_info.value).lower()
        clear_allowed_model_objects()

    def test_live_ownership_check_refuses_replacing_view_without_semabridge_comment(self):
        set_allowed_model_objects({"MY_ANALYTICS_VIEW"})
        mock_cursor = MagicMock()
        # Mock SHOW VIEWS returning a view with an external user's comment
        mock_cursor.description = [("name",), ("schema_name",), ("comment",)]
        mock_cursor.fetchall.return_value = [
            ("MY_ANALYTICS_VIEW", "PUBLIC", "Created by external data science team"),
        ]

        # Validating replacing this view with live ownership check must fail!
        with pytest.raises(SQLOwnershipSecurityError) as exc_info:
            validate_safe_sql(
                'CREATE OR REPLACE VIEW "MY_ANALYTICS_VIEW" AS SELECT 1',
                cursor=mock_cursor,
                check_ownership=True,
            )
        assert "not created by semabridge" in str(exc_info.value).lower()
        clear_allowed_model_objects()

    def test_live_ownership_check_allows_replacing_view_with_semabridge_comment(self):
        set_allowed_model_objects({"SALES_FACT_ENRICHED"})
        mock_cursor = MagicMock()
        mock_cursor.description = [("name",), ("schema_name",), ("comment",)]
        mock_cursor.fetchall.return_value = [
            ("SALES_FACT_ENRICHED", "SEMABRIDGE_WORKSPACE", f"{SEMABRIDGE_SIGNATURE_COMMENT} - auto helper view"),
        ]

        # Replacing a view that carries the signature comment passes!
        validate_safe_sql(
            'CREATE OR REPLACE VIEW "SALES_FACT_ENRICHED" AS SELECT 1',
            cursor=mock_cursor,
            check_ownership=True,
        )
        clear_allowed_model_objects()

    def test_after_timestamp_update_ownership_check_still_passes(self):
        set_allowed_model_objects({"CORPORATE_SPEND_SEMANTIC"})
        mock_cursor = MagicMock()
        mock_cursor.description = [("name",), ("schema_name",), ("comment",)]
        # After a timestamp update where SemaBridge writes updated timestamp + signature
        mock_cursor.fetchall.return_value = [
            ("CORPORATE_SPEND_SEMANTIC", "SEMABRIDGE_WORKSPACE", f"{SEMABRIDGE_SIGNATURE_COMMENT} | Last updated: 2026-10-12T15:30:00Z"),
        ]
        # Subsequent CREATE OR REPLACE or DROP statement must still pass ownership verification!
        validate_safe_sql(
            'CREATE OR REPLACE SEMANTIC VIEW "CORPORATE_SPEND_SEMANTIC" TABLES (t1 AS SELECT 1)',
            cursor=mock_cursor,
            check_ownership=True,
        )
        clear_allowed_model_objects()


class TestDataIntegrityChecker:
    """Tests that the data integrity checker verifies row counts and non-null counts accurately."""

    def test_integrity_check_passes_on_exact_match(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            (4,),          # SELECT COUNT(*)
            (4, 4, 4),     # SELECT COUNT("CATEGORY"), COUNT("CHANNEL"), COUNT("SORT")
        ]

        table_info = {
            "row_count": 4,
            "columns": {
                "CATEGORY": {"non_null_count": 4},
                "CHANNEL": {"non_null_count": 4},
                "SORT": {"non_null_count": 4},
            },
        }

        disc = check_table_integrity(mock_cursor, "CATEGORY", table_info)
        assert disc == []

    def test_integrity_check_detects_row_count_mismatch(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            (3,),          # Live row count 3, expected 4
            (3, 3, 3),
        ]

        table_info = {
            "row_count": 4,
            "columns": {
                "CATEGORY": {"non_null_count": 4},
                "CHANNEL": {"non_null_count": 4},
                "SORT": {"non_null_count": 4},
            },
        }

        disc = check_table_integrity(mock_cursor, "CATEGORY", table_info)
        assert len(disc) >= 1
        assert "Row count mismatch" in disc[0]

    def test_integrity_check_detects_column_non_null_mismatch(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [
            (4,),          # Row count matches (4)
            (4, 2, 4),     # CHANNEL has only 2 non-nulls instead of 4!
        ]

        table_info = {
            "row_count": 4,
            "columns": {
                "CATEGORY": {"non_null_count": 4},
                "CHANNEL": {"non_null_count": 4},
                "SORT": {"non_null_count": 4},
            },
        }

        disc = check_table_integrity(mock_cursor, "CATEGORY", table_info)
        assert len(disc) == 1
        assert "Column CHANNEL: Non-null count mismatch" in disc[0]
