from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.utils.identifiers import IdentifierSanitizer


class _SchemaManager:
    def _resolve_physical_column_name(self, dataset, column):
        wanted = str(column or "").casefold()
        for col in getattr(dataset, "columns", []) or []:
            name = str(getattr(col, "unique_name", "") or "")
            if name.casefold() == wanted:
                return name
        return str(column or "").upper()


class _Sanitizer:
    def format_physical_column_ref(self, alias, column, model_name=None):
        return f'{alias}."{column}"'


class _Translator:
    def _resolve_column_name_for_dataset(self, known_columns, candidate):
        wanted = str(candidate or "").replace("_", "").casefold()
        for column in known_columns:
            if str(column).replace("_", "").casefold() == wanted:
                return column
        return None

    def _auto_qualify_cross_table_refs(self, expr, dataset_aliases):
        return expr

    def _normalize_metric_column_references(self, expr, *args, **kwargs):
        return expr

    def _validate_metric_column_references(self, expr, *args, **kwargs):
        return True, None


class _Cursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def close(self):
        pass


class _ConnectionManager:
    def __init__(self, connection):
        self.connection = connection

    def get_connection(self):
        return self.connection, True


def test_direct_aggregate_uses_physical_lineage_not_metric_alias() -> None:
    metric = SimpleNamespace(
        unique_name="SUM_OF_REVENUE",
        name="SUM_OF_REVENUE",
        dataset="SalesFact",
        source_column="SUM_OF_REVENUE",
        aggregation=SimpleNamespace(value="sum"),
        expression="SUM('SalesFact'[Revenue])",
        sql_expression=None,
    )
    dataset = SimpleNamespace(unique_name="SalesFact", columns=[SimpleNamespace(unique_name="REVENUE")])
    builder = MetricsClauseBuilder(
        IdentifierSanitizer(force_uppercase=True),
        _SchemaManager(),
        _Sanitizer(),
        _Translator(),
        SimpleNamespace(database="DB", schema_name="SCHEMA"),
    )

    expr = builder._generate_metric_expression(
        metric,
        "SUM_OF_REVENUE",
        "SALESFACT",
        SimpleNamespace(metrics=[metric], datasets=[dataset]),
        {"SalesFact": dataset},
        {"SalesFact": "SALESFACT"},
        {"SalesFact": {"REVENUE"}},
        {},
        {"SUM_OF_REVENUE"},
        {"REVENUE"},
        set(),
        set(),
        "Model",
        False,
    )

    assert expr == 'SUM(SALESFACT."REVENUE")'
    assert 'SUM_OF_REVENUE' not in expr


def test_failed_measure_persistence_uses_safe_minimal_schema() -> None:
    cursor = _Cursor()
    emitter = SnowflakeEmitter(SimpleNamespace(database="DB", schema_name="SCHEMA"))
    emitter.connection_manager = _ConnectionManager(_Connection(cursor))

    emitter._store_failed_measures(
        [
            {
                "measure_name": "Broken Metric",
                "dax_expression": "[Missing]",
                "generated_sql": "",
                "error_message": "missing dependency",
            }
        ]
    )

    create_sql = cursor.executed[0][0].lower()
    insert_sql, params = cursor.executed[1]
    assert "create table if not exists" in create_sql
    assert "measure_name varchar(500)" in create_sql
    assert "error_message text" in create_sql
    assert "(measure_name, error_message)" in insert_sql
    assert params == ("Broken Metric", "missing dependency")
