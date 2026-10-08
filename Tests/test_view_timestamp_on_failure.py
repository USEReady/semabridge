"""On a failed deploy, the previously deployed semantic view is left intact
and only its LAST_ALTERED timestamp is touched -- in the run's configured
database/schema, on the model's own deployed view name."""

from types import SimpleNamespace

import pytest

from semabridge.core.engine.deployment import snowflake as dep_sf
from semabridge.core.settings import SnowflakeConfig


class _Cursor:
    def __init__(self, show_row, comment_col=True):
        self.sql = []
        self._row = show_row
        self.description = [("name",), ("comment",)] if comment_col else [("name",)]

    def execute(self, sql):
        self.sql.append(sql)

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def _run(monkeypatch, cursor, *, view="MODEL_A_SEMANTIC", schema="SCHEMA_X", database="DB_X"):
    import snowflake.connector

    conn = _Conn(cursor)
    monkeypatch.setattr(snowflake.connector, "connect", lambda **kw: conn)
    monkeypatch.setattr(
        "semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", lambda cfg: {}
    )
    cfg = SnowflakeConfig(account="acc", user="usr", database=database, schema_name=schema)
    context = SimpleNamespace(deployed_view_name=view, project_id="proj-unrelated")
    return dep_sf._update_snowflake_view_metadata_on_failure(None, context, cfg, "boom"), conn


def test_touches_the_models_semantic_view_in_the_configured_schema(monkeypatch):
    cursor = _Cursor(show_row=("MODEL_A_SEMANTIC", "existing comment"))
    ok, conn = _run(monkeypatch, cursor)
    assert ok is True
    assert conn.closed
    assert cursor.sql[0] == "SHOW SEMANTIC VIEWS LIKE 'MODEL_A_SEMANTIC' IN SCHEMA \"DB_X\".\"SCHEMA_X\""
    assert cursor.sql[1] == (
        'ALTER SEMANTIC VIEW IF EXISTS "DB_X"."SCHEMA_X"."MODEL_A_SEMANTIC" SET COMMENT = \'existing comment\''
    )
    assert all("proj-unrelated" not in sql for sql in cursor.sql)


def test_view_without_a_comment_is_touched_without_adding_one(monkeypatch):
    cursor = _Cursor(show_row=("MODEL_A_SEMANTIC", None))
    ok, _ = _run(monkeypatch, cursor)
    assert ok is True
    assert cursor.sql[1] == 'ALTER SEMANTIC VIEW IF EXISTS "DB_X"."SCHEMA_X"."MODEL_A_SEMANTIC" UNSET COMMENT'


def test_missing_view_is_left_alone(monkeypatch):
    cursor = _Cursor(show_row=None)
    ok, _ = _run(monkeypatch, cursor)
    assert ok is False
    assert len(cursor.sql) == 1  # only the existence check


@pytest.mark.parametrize("override", [{"view": ""}, {"schema": ""}, {"database": ""}])
def test_unknown_target_does_nothing_and_uses_no_hardcoded_fallback(monkeypatch, override):
    cursor = _Cursor(show_row=("MODEL_A_SEMANTIC", None))
    if override.get("schema") == "":
        # An empty schema_name falls back to SnowflakeConfig's own default;
        # simulate a config where it is genuinely unknown.
        import snowflake.connector

        monkeypatch.setattr(snowflake.connector, "connect", lambda **kw: _Conn(cursor))
        cfg = SimpleNamespace(database="DB_X", schema_name="")
        context = SimpleNamespace(deployed_view_name="MODEL_A_SEMANTIC")
        assert dep_sf._update_snowflake_view_metadata_on_failure(None, context, cfg, "boom") is False
    else:
        ok, _ = _run(monkeypatch, cursor, **override)
        assert ok is False
    assert cursor.sql == []
