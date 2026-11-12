"""Semantic-view DDL must survive a date column stored as TEXT in Snowflake.

Real failure this guards: 'SQL compilation error: Function EXTRACT does not
support VARCHAR(500) argument type' -- YEAR(SALESFACT."COL_DATE") where the
bound table/view stores COL_DATE as TEXT. Existing tables are never altered,
so the DDL must coerce instead. Nothing here is specific to that model.
"""

from semabridge.connectors.date_column_coercion import (
    alias_tables,
    coerce_ddls,
    coerce_text_date_columns,
    is_text_type,
)

DDL = '''CREATE OR REPLACE SEMANTIC VIEW "DB"."SCH"."V"
TABLES (
  F AS "DB"."SCH"."FACT_ENRICHED",
  "D" AS "DB"."SCH"."CAL" PRIMARY KEY ("DT"),
  P AS PRODUCT
)
FACTS (
  F."ORDER_DT" AS F."ORDER_DT"
)
DIMENSIONS (
  D."DT" AS D."DT"
)
METRICS (
  F."PY_UNITS" AS SUM(CASE WHEN YEAR(F."ORDER_DT") = YEAR(DATEADD(YEAR, -1, CURRENT_DATE())) THEN F."UNITS" END),
  F."YTD" AS SUM(CASE WHEN F."ORDER_DT" >= DATE_TRUNC('YEAR', F."ORDER_DT") THEN F."UNITS" END),
  F."MONTHS" AS COUNT(DISTINCT EXTRACT(MONTH FROM F."ORDER_DT")),
  F."NESTED" AS MAX(MONTH(DATEADD(DAY, 1, F."ORDER_DT"))),
  F."TEXT_ONLY" AS COUNT(F."ORDER_DT"),
  F."CODE_YEAR" AS MAX(YEAR(F."SHIP_DT")),
  D."CAL_YEAR" AS MAX(YEAR(D."DT"))
);'''

TYPES = {
    "FACT_ENRICHED": {"ORDER_DT": "TEXT", "UNITS": "NUMBER", "SHIP_DT": "DATE"},
    "CAL": {"DT": "DATE"},
    "PRODUCT": {"NAME": "TEXT"},
}


def test_alias_tables_reads_the_tables_clause():
    assert alias_tables(DDL) == {"F": "FACT_ENRICHED", "D": "CAL", "P": "PRODUCT"}


def test_text_date_column_is_coerced_only_inside_date_functions():
    out, wrapped = coerce_text_date_columns(DDL, TYPES)
    assert wrapped == [("F", "ORDER_DT")]
    assert 'YEAR(TRY_TO_TIMESTAMP(F."ORDER_DT"))' in out
    assert 'DATE_TRUNC(\'YEAR\', TRY_TO_TIMESTAMP(F."ORDER_DT"))' in out
    assert 'EXTRACT(MONTH FROM TRY_TO_TIMESTAMP(F."ORDER_DT"))' in out
    assert 'MONTH(DATEADD(DAY, 1, TRY_TO_TIMESTAMP(F."ORDER_DT")))' in out
    # outside date functions the column is left alone (comparisons cast implicitly)
    assert 'F."ORDER_DT" AS F."ORDER_DT"' in out
    assert 'COUNT(F."ORDER_DT")' in out
    assert 'WHEN F."ORDER_DT" >= DATE_TRUNC' in out


def test_real_date_columns_are_untouched():
    out, _ = coerce_text_date_columns(DDL, TYPES)
    assert 'YEAR(F."SHIP_DT")' in out
    assert 'YEAR(D."DT")' in out


def test_no_text_dates_means_identical_ddl():
    clean = {"FACT_ENRICHED": {"ORDER_DT": "DATE"}, "CAL": {"DT": "DATE"}}
    out, wrapped = coerce_text_date_columns(DDL, clean)
    assert out == DDL and wrapped == []
    assert coerce_text_date_columns(DDL, {}) == (DDL, [])


def test_idempotent():
    once, _ = coerce_text_date_columns(DDL, TYPES)
    twice, wrapped = coerce_text_date_columns(once, TYPES)
    assert twice == once and wrapped == []


def test_string_literals_are_not_parsed_as_code():
    ddl = DDL.replace("COUNT(F.\"ORDER_DT\")", "COUNT(F.\"ORDER_DT\") COMMENT = 'YEAR(F.\"ORDER_DT\") note)'")
    out, _ = coerce_text_date_columns(ddl, TYPES)
    assert "COMMENT = 'YEAR(F.\"ORDER_DT\") note)'" in out


def test_varchar_variants_count_as_text():
    assert is_text_type("TEXT") and is_text_type("VARCHAR(500)") and is_text_type("varchar")
    assert not is_text_type("DATE") and not is_text_type("TIMESTAMP_NTZ") and not is_text_type("NUMBER")


def test_coerce_ddls_passes_non_semantic_statements_through():
    out, wrapped = coerce_ddls(["", "ALTER SESSION SET X = 1", DDL], TYPES)
    assert out[0] == "" and out[1] == "ALTER SESSION SET X = 1"
    assert wrapped == [("F", "ORDER_DT")]


def test_emitter_hook_reads_live_types_and_never_blocks():
    from semabridge.connectors.snowflake_emitter_parts.deploy_isolation import DeployIsolationMixin

    class Cfg:
        database = "DB"
        schema_name = "SCH"

    class Cur:
        def __init__(self, rows=None, fail=False):
            self.rows, self.fail = rows or [], fail

        def execute(self, *a, **k):
            if self.fail:
                raise RuntimeError("no access")

        def fetchall(self):
            return self.rows

    class E(DeployIsolationMixin):
        config = Cfg()
        _type_coercion_notes: list = []

    rows = [(t, c, dt) for t, cols in TYPES.items() for c, dt in cols.items()]
    e = E()
    out = e._coerce_text_date_columns(Cur(rows), [DDL])
    assert 'YEAR(TRY_TO_TIMESTAMP(F."ORDER_DT"))' in out[0]
    assert e._type_coercion_notes == ["F.ORDER_DT"]
    assert E()._coerce_text_date_columns(Cur(fail=True), [DDL]) == [DDL]
