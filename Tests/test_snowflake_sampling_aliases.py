from types import SimpleNamespace

from semabridge.connectors.schema_manager import SnowflakeSchemaManager, _sample_alias as schema_manager_sample_alias
from semabridge.connectors.snowflake_emitter_parts.schema_evolution import infer_columns_from_table_samples
from semabridge.connectors.snowflake_emitter_parts.schema_evolution import _sample_alias as schema_evolution_sample_alias
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.utils.identifiers import IdentifierSanitizer


LONG_ALIAS = (
    "SEVERITY_GROUPS_IF_REP_SFDC_RAIL_CASE_CSEVERITY_1_URGENT_REP_SFDC_RAIL_CASE_"
    "CSEVERITY_2_VERY_HIGH_REP_SFDC_RAIL_CASE_CSEVERITY_3_HIGH_1_URGENT_IF_REP_"
    "SFDC_RAIL_CASE_CSEVERITY_4_MEDIUM_2_AT_RISK_3_ON_TIME_USED_FOR_FILTERING_THE_"
    "PROJECT_DASHBOARD_S_DELIVERY_STATUS_SUMMARY_TAB_PER_THE_CUSTOMER_SUCCESS_LEADERSHIP_ASK"
)


class _FakeCursor:
    def __init__(self) -> None:
        self._results = []

    def fetchall(self):
        return self._results.pop(0) if self._results else []


class _FakeConnectionManager:
    def __init__(self) -> None:
        self.executed = []

    def _execute_sql(self, cursor, sql: str, params=None, context: str = ""):
        self.executed.append((context, sql))
        if context.startswith("INFORMATION_SCHEMA.COLUMNS"):
            cursor._results.append([(LONG_ALIAS.replace("_", ""), "VARCHAR")])
        elif context.startswith("SAMPLE QUERY"):
            cursor._results.append([("value",)])
        return None


class _FakeEmitter:
    def __init__(self) -> None:
        self.config = SimpleNamespace(database="DB", schema_name="SCHEMA")
        self.executed = []

    def _execute_sql(self, cursor, sql: str, params=None, context: str = ""):
        self.executed.append((context, sql))
        if context.startswith("INFORMATION_SCHEMA.COLUMNS"):
            cursor._results.append([(LONG_ALIAS.replace("_", ""), "VARCHAR")])
        elif context.startswith("SAMPLE QUERY"):
            cursor._results.append([("value",)])
        return None


def test_sample_alias_helpers_are_short():
    assert schema_manager_sample_alias(0) == "SAMPLE_COL_1"
    assert schema_evolution_sample_alias(0) == "SAMPLE_COL_1"


def test_schema_manager_sample_query_uses_short_aliases():
    manager = SnowflakeSchemaManager(
        config=SnowflakeConfig(database="DB", schema_name="SCHEMA"),
        behavior=ConnectorBehavior(),
        identifier_sanitizer=IdentifierSanitizer(),
        connection_manager=_FakeConnectionManager(),
    )
    cursor = _FakeCursor()

    inferred, logs = manager._infer_columns_from_table_samples(
        cursor,
        safe_table_name="SAFE_TABLE",
        columns=[{"name": LONG_ALIAS, "type": "VARCHAR"}],
    )

    assert inferred
    assert logs

    sample_sql = next(sql for context, sql in manager.connection_manager.executed if context.startswith("SAMPLE QUERY"))
    assert LONG_ALIAS not in sample_sql
    assert 'AS "SAMPLE_COL_1"' in sample_sql


def test_schema_evolution_sample_query_uses_short_aliases():
    emitter = _FakeEmitter()
    cursor = _FakeCursor()

    inferred, logs = infer_columns_from_table_samples(
        emitter,
        cursor,
        safe_table_name="SAFE_TABLE",
        columns=[{"name": LONG_ALIAS, "type": "VARCHAR"}],
    )

    assert inferred
    assert logs

    sample_sql = next(sql for context, sql in emitter.executed if context.startswith("SAMPLE QUERY"))
    assert LONG_ALIAS not in sample_sql
    assert 'AS "SAMPLE_COL_1"' in sample_sql
