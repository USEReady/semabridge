"""Read-only SQL guard: exactly one read statement; every write or side effect is blocked."""
import pytest

from semabridge.connectors.sql_guard import SQLSecurityGuardError, validate_read_only_sql as guard

ALLOWED = [
    "SELECT 1", "select * from t", "  SELECT a FROM t  ", "SELECT a FROM t;", "WITH x AS (SELECT 1) SELECT * FROM x",
    "with a as (select 1), b as (select 2) select * from a, b", "SHOW TABLES", "show semantic views in schema db.s",
    "DESCRIBE SEMANTIC VIEW db.s.v", "DESC TABLE t", "SELECT * FROM SEMANTIC_VIEW(db.s.v METRICS t.m DIMENSIONS t.d)",
    "SELECT 'drop table x' AS note", "SELECT * FROM t WHERE c = 'DELETE FROM t'", "SELECT update_time FROM t",
    "SELECT created_at, deleted_flag FROM t", "SELECT * FROM t -- DROP TABLE t", "SELECT /* insert */ 1",
    "SELECT SYSTEM$TYPEOF(1)", "SELECT * FROM t QUALIFY ROW_NUMBER() OVER (ORDER BY a) = 1",
    "SELECT COUNT(*) FROM t WHERE merge_key IS NULL", "SELECT a AS \"grant\" FROM t", "SELECT GET_DDL('VIEW','v')",
    "(SELECT 1)", "SELECT 1 UNION ALL SELECT 2", "SELECT * FROM t LIMIT 10 OFFSET 5",
    # column and function names that look like keywords must not be blocked (statement type decides)
    "SELECT comment, list, set, use FROM t", "SELECT t.comment FROM t WHERE t.list = 1",
    "SELECT GET(v, 'a') FROM t", "SELECT GET_PATH(v, 'a.b') FROM t", "SELECT \"USE\" FROM t",
]
BLOCKED = [
    "", "   ", ";", "DROP TABLE t", "drop view v", "DELETE FROM t", "UPDATE t SET a=1", "INSERT INTO t VALUES (1)",
    "MERGE INTO t USING s ON t.a=s.a WHEN MATCHED THEN DELETE", "TRUNCATE TABLE t", "CREATE TABLE t (a int)",
    "CREATE OR REPLACE VIEW v AS SELECT 1", "CREATE OR REPLACE SEMANTIC VIEW v TABLES (t)", "ALTER TABLE t ADD c int",
    "GRANT SELECT ON t TO ROLE r", "REVOKE SELECT ON t FROM ROLE r", "CALL proc()", "EXECUTE IMMEDIATE 'DROP TABLE t'",
    "COPY INTO t FROM @s", "PUT file:///x @s", "GET @s file:///x", "USE ROLE ACCOUNTADMIN", "USE WAREHOUSE w",
    "UNDROP TABLE t", "COMMENT ON TABLE t IS 'x'", "SELECT 1; DROP TABLE t", "SELECT 1; SELECT 2",
    "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x", "WITH x AS (DELETE FROM t) SELECT 1",
    "SELECT SYSTEM$CANCEL_QUERY('q')", "select system$abort_session(1)", "SELECT SYSTEM$CANCEL_ALL_QUERIES(1)",
    "/* c */ DROP TABLE t", "-- c\nDELETE FROM t", "BEGIN", "COMMIT", "ROLLBACK", "SET x = 1", "UNSET x",
    "CREATE TEMPORARY TABLE t AS SELECT 1", "INSERT OVERWRITE INTO t SELECT 1", "REMOVE @s", "LIST @s",
    "ALTER SESSION SET QUERY_TAG='x'", "SeLeCt 1;DeLeTe FrOm t",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_single_read_statement_is_allowed(sql):
    assert guard(sql, warn_only=False) is True


@pytest.mark.parametrize("sql", BLOCKED)
def test_writes_side_effects_and_multi_statements_are_blocked(sql):
    with pytest.raises(SQLSecurityGuardError):
        guard(sql, warn_only=False)


@pytest.mark.parametrize("sql", ["DROP TABLE t", "DELETE FROM t"])
def test_warn_mode_returns_false_instead_of_raising(sql):
    assert guard(sql, warn_only=True) is False
