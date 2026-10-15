"""A metric must never make the sync fail: untranslatable or Snowflake-rejected
metrics become CAST(NULL AS DOUBLE) placeholders; everything else is unchanged."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.sml.models import SMLModel
from semabridge.utils.identifiers import IdentifierSanitizer
from test_metric_drop_ledger_coverage import _builder, _model
from test_snowflake_emitter_findings_regressions import _build_emitter_for_ddl_loop

DDL = """CREATE OR REPLACE SEMANTIC VIEW "DB"."SCH"."MODEL"
TABLES (
  SALESFACT AS "DB"."SCH"."SALES_FACT" PRIMARY KEY ("ID"),
  PRODUCT AS "DB"."SCH"."PRODUCT" PRIMARY KEY ("PRODUCT_ID")
)
RELATIONSHIPS (
  SALESFACT_PRODUCT AS SALESFACT ("PRODUCT_ID") REFERENCES PRODUCT ("PRODUCT_ID")
)
DIMENSIONS (
  PRODUCT."CATEGORY" AS PRODUCT."CATEGORY"
)
METRICS (
  SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT."UNITS"),
  SALESFACT."UNTRANSLATABLE" AS CAST(NULL AS DOUBLE),
  SALESFACT."REJECTED" AS SUM(SALESFACT."NO_SUCH_COL"),
  SALESFACT."REVENUE" AS SUM(SALESFACT."REVENUE")
);"""


def test_untranslatable_metric_is_emitted_as_null_placeholder_on_home_table():
    builder = _builder(translator=MetricExpressionTranslator(IdentifierSanitizer()))
    untranslatable = SimpleNamespace(
        unique_name="Untranslatable", dataset="SalesFact", expression="",
        sql_expression=None, source_column=None, aggregation=None,
    )
    good = SimpleNamespace(
        unique_name="Total Units", dataset="SalesFact", expression="",
        sql_expression=None, source_column="Units", aggregation=SimpleNamespace(value="sum"),
    )

    lines = builder.build_for_osi(
        _model([untranslatable, good]),
        dataset_aliases={"SalesFact": "SALESFACT"},
        dataset_by_name={"SalesFact": SimpleNamespace(is_fact=True)},
        dataset_col_lookup={"SalesFact": {"UNITS"}},
        alias_by_raw={},
        all_physical_col_names={"UNITS"},
        emittable_metric_name_set=set(),
    )

    assert lines[0].rstrip(",") == '  SALESFACT."UNTRANSLATABLE" AS CAST(NULL AS DOUBLE)'
    assert 'SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT."UNITS")' in lines[1]
    records = builder.drop_ledger.to_json()
    assert [(r["entity_name"], r["reason"]) for r in records] == [
        ("Untranslatable", "placeholder: not translatable"),
    ]


def _wire(emitter, monkeypatch, fail_with):
    """One DDL; Snowflake rejects it while `fail_with(sql)` returns an error."""
    conn = MagicMock()
    monkeypatch.setattr(emitter.connection_manager, "get_connection", lambda: (conn, True))
    monkeypatch.setattr(emitter.semantic_view_builder, "_precompute_duplicate_mappings", lambda *a, **k: None)
    monkeypatch.setattr(emitter.schema_manager, "refresh_schema_cache", lambda: None)
    monkeypatch.setattr(emitter.schema_manager, "_fetch_schema_metadata", lambda cur: {"DUMMY": set()})
    monkeypatch.setattr(emitter.semantic_view_builder, "generate_ddls", lambda model: [DDL])
    executed = []

    def fake_execute_sql(cursor, sql, context=""):
        if context.startswith("DDL[0]"):
            executed.append(sql)
            error = fail_with(sql)
            if error:
                raise error

    monkeypatch.setattr(emitter.connection_manager, "_execute_sql", fake_execute_sql)
    return executed


def _model_stub():
    return SMLModel(unique_name="M", label="M", datasets=[], metrics=[], relationships=[], dimensions=[])


def test_snowflake_rejected_metric_is_nulled_and_view_deploys(monkeypatch):
    emitter = _build_emitter_for_ddl_loop()
    executed = _wire(
        emitter, monkeypatch,
        lambda sql: RuntimeError("000904 (42000): SQL compilation error: invalid identifier 'SALESFACT.NO_SUCH_COL'")
        if "NO_SUCH_COL" in sql else None,
    )

    assert emitter.deploy(_model_stub(), sync_mode="copy") is True

    final = executed[-1]
    assert 'SALESFACT."REJECTED" AS CAST(NULL AS DOUBLE),' in final
    assert 'SALESFACT."UNTRANSLATABLE" AS CAST(NULL AS DOUBLE),' in final
    # Everything else is byte-identical to the generated DDL.
    assert final == DDL.replace('SUM(SALESFACT."NO_SUCH_COL")', "CAST(NULL AS DOUBLE)")
    records = emitter.drop_ledger.to_json()
    rejected = [r for r in records if r["entity_name"] == "REJECTED"]
    assert len(rejected) == 1
    assert rejected[0]["reason"].startswith("placeholder: rejected by Snowflake: 000904")


def test_rejection_not_caused_by_a_metric_fails_without_touching_the_ddl(monkeypatch):
    emitter = _build_emitter_for_ddl_loop()
    executed = _wire(
        emitter, monkeypatch,
        lambda sql: RuntimeError("000904: invalid identifier 'PRODUCT.CATEGORY'"),
    )

    assert emitter.deploy(_model_stub(), sync_mode="copy") is False
    assert executed == [DDL]  # dimensions/relationships are never removed
    assert emitter.drop_ledger.to_json() == []


def test_remediation_stops_after_five_attempts(monkeypatch):
    emitter = _build_emitter_for_ddl_loop()
    names = iter(["TOTAL_UNITS", "REVENUE", "REJECTED", "UNTRANSLATABLE", "TOTAL_UNITS", "REVENUE", "X"])
    executed = _wire(
        emitter, monkeypatch,
        lambda sql: RuntimeError(f"010220: Invalid metric definition for 'SALESFACT.{next(names)}': boom"),
    )

    assert emitter.deploy(_model_stub(), sync_mode="copy") is False
    assert len(executed) == 1 + 5  # first attempt + at most 5 remediated retries
