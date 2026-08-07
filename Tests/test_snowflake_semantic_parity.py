from __future__ import annotations

import sys
from types import SimpleNamespace

from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder
from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer


class _DDL:
    def sanitize_semantic_name(self, name: str) -> str:
        return IdentifierSanitizer().sanitize_column(name)

    def format_physical_column_ref(self, alias: str, column: str, model_name: str | None = None) -> str:
        return f'{alias}."{column}"'

    def to_snowflake_relationship_name(self, name: str) -> str:
        return IdentifierSanitizer().sanitize_column(name)


class _Schema:
    def _resolve_physical_column_name(self, dataset, column: str) -> str:
        del dataset
        return IdentifierSanitizer().sanitize_column(column)


def test_dimensions_emit_one_semantic_dimension_per_physical_column():
    id_sanitizer = IdentifierSanitizer()
    builder = DimensionsClauseBuilder(
        id_sanitizer,
        _Schema(),
        _DDL(),
        translator=None,
        behavior=SimpleNamespace(semantic_model=SimpleNamespace(sync_all_attributes=True)),
    )
    col = SimpleNamespace(unique_name="Diversity_Key", label="Diversity_Key", synonyms=[])
    dataset = SimpleNamespace(unique_name="diversity_dim", columns=[col])
    dim = SimpleNamespace(
        attributes=[
            SimpleNamespace(
                dataset="diversity_dim",
                unique_name="Diversity Key",
                source_column="Diversity_Key",
                dataset_column="Diversity_Key",
            )
        ]
    )

    lines = builder.build_for_osi(
        SimpleNamespace(dimensions=[dim], datasets=[dataset], unique_name="model", label="model"),
        dataset_aliases={"diversity_dim": "DIVERSITY_DIM"},
        dataset_by_name={"diversity_dim": dataset},
        dataset_col_lookup={"diversity_dim": {"DIVERSITY_KEY"}},
        measure_columns=set(),
    )

    assert len(lines) == 1
    assert 'DIVERSITY_DIM."DIVERSITY_KEY"' in lines[0]


def test_relationship_builder_keeps_inactive_fabric_relationships():
    builder = RelationshipsClauseBuilder(IdentifierSanitizer(), _Schema(), _DDL())
    rel = SimpleNamespace(
        unique_name="spend_fact_Posting_Date_date_LY_CAL_DT_inactive",
        is_active=False,
        from_dataset="spend_fact",
        to_dataset="date",
        from_columns=["Posting_Date"],
        to_columns=["LY_CAL_DT"],
    )

    lines = builder.build_for_osi(
        SimpleNamespace(relationships=[rel]),
        dataset_aliases={"spend_fact": "SPEND_FACT", "date": "COL_DATE"},
        dataset_by_name={"spend_fact": SimpleNamespace(), "date": SimpleNamespace()},
        dataset_col_lookup={"spend_fact": {"POSTING_DATE"}, "date": {"LY_CAL_DT"}},
        declared_pk_by_alias={},
        relationship_target_alias={},
    )

    assert lines == [
        '  SPEND_FACT_POSTING_DATE_DATE_LY_CAL_DT_INACTIVE AS SPEND_FACT ("POSTING_DATE") REFERENCES COL_DATE ("LY_CAL_DT")'
    ]


def test_openai_dax_translation_is_attempted_when_key_is_configured(monkeypatch):
    calls = []

    class _Message:
        content = 'SUM(CASE WHEN DIVERSITY_BRIDGE."DIVERSITY_FLAG" = \'Y\' THEN SPEND_FACT."TRANSACTION_USD_AMOUNT" ELSE 0 END)'

    class _Choice:
        message = _Message()

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[_Choice()])

    class _Chat:
        completions = _Completions()

    class _OpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = _Chat()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_OpenAI))

    translator = MetricExpressionTranslator(IdentifierSanitizer())
    sql = translator._try_openai_dax_translation(
        dax_expression='CALCULATE(SUM(spend_fact[Transaction_USD_Amount]), FILTER(diversity_bridge, diversity_bridge[Diversity_Flag] = "Y"))',
        metric=SimpleNamespace(unique_name="DIVERSE_SUPPLIER_SPEND", dataset="spend_fact"),
        table_alias="SPEND_FACT",
        dataset_col_lookup={
            "spend_fact": {"TRANSACTION_USD_AMOUNT"},
            "diversity_bridge": {"DIVERSITY_FLAG"},
        },
    )

    assert calls
    assert sql.startswith("SUM(CASE WHEN")


def test_openai_batch_prefetch_is_used_before_single_call(monkeypatch):
    calls = []

    class _Message:
        content = '{"DIVERSE_SUPPLIER_SPEND":"SUM(CASE WHEN DIVERSITY_BRIDGE.\\"DIVERSITY_FLAG\\" = \'Y\' THEN SPEND_FACT.\\"TRANSACTION_USD_AMOUNT\\" ELSE 0 END)"}'

    class _Choice:
        message = _Message()

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[_Choice()])

    class _Chat:
        completions = _Completions()

    class _OpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.chat = _Chat()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=_OpenAI))

    translator = MetricExpressionTranslator(IdentifierSanitizer())
    metric = SimpleNamespace(
        unique_name="DIVERSE_SUPPLIER_SPEND",
        dataset="spend_fact",
        expression='CALCULATE(SUM(spend_fact[Transaction_USD_Amount]), FILTER(diversity_bridge, diversity_bridge[Diversity_Flag] = "Y"))',
    )
    translator.prefetch_openai_metric_translations(
        metrics=[metric],
        table_alias="SPEND_FACT",
        dataset_col_lookup={
            "spend_fact": {"TRANSACTION_USD_AMOUNT"},
            "diversity_bridge": {"DIVERSITY_FLAG"},
        },
    )
    sql = translator._try_llm_metric_fallback_expression(
        metric=metric,
        metric_name="DIVERSE_SUPPLIER_SPEND",
        table_alias="SPEND_FACT",
        alias_by_raw={},
        dataset_col_lookup={
            "spend_fact": {"TRANSACTION_USD_AMOUNT"},
            "diversity_bridge": {"DIVERSITY_FLAG"},
        },
        dataset_aliases={"spend_fact": "SPEND_FACT", "diversity_bridge": "DIVERSITY_BRIDGE"},
        metric_name_set=set(),
        all_physical_col_names={"TRANSACTION_USD_AMOUNT", "DIVERSITY_FLAG"},
        emittable_metric_name_set=set(),
        skipped_metric_names=set(),
    )

    assert calls
    assert sql.startswith("SUM(CASE WHEN")


def test_display_name_metric_references_are_normalized():
    translator = MetricExpressionTranslator(IdentifierSanitizer())

    sql = translator._normalize_metric_column_references(
        'CASE WHEN "Total VanArsdel Units R12M" = 0 THEN 0 ELSE "Total VanArsdel Units R12M" / NULLIF("Total Units R12Ms", 0) END',
        metric_name="PCT_UNITS_MARKET_SHARE_R12M",
        dataset_col_lookup={"SalesFact": {"UNITS"}},
        dataset_aliases={"SalesFact": "SALESFACT"},
        metric_names={
            "TOTAL_VANARSDEL_UNITS_R12M",
            "TOTAL_UNITS_R12MS",
            "PCT_UNITS_MARKET_SHARE_R12M",
        },
        preferred_table_alias="SALESFACT",
        metric_to_alias={
            "TOTAL_VANARSDEL_UNITS_R12M": "SALESFACT",
            "TOTAL_UNITS_R12MS": "SALESFACT",
            "PCT_UNITS_MARKET_SHARE_R12M": "SALESFACT",
        },
    )

    assert '"Total VanArsdel Units R12M"' not in sql
    assert '"Total Units R12Ms"' not in sql
    assert 'SALESFACT."TOTAL_VANARSDEL_UNITS_R12M"' in sql
    assert 'SALESFACT."TOTAL_UNITS_R12MS"' in sql

    qualified_sql = translator._normalize_metric_column_references(
        'CASE WHEN "salesfact"."% UNIT MARKET SHARE YOY CHANGE" < 0 THEN 1 ELSE 2 END',
        metric_name="ATINDICATOR02",
        dataset_col_lookup={"SalesFact": {"UNITS"}},
        dataset_aliases={"SalesFact": "SALESFACT"},
        metric_names={"PCT_UNIT_MARKET_SHARE_YOY_CHANGE", "ATINDICATOR02"},
        preferred_table_alias="SALESFACT",
        metric_to_alias={
            "PCT_UNIT_MARKET_SHARE_YOY_CHANGE": "SALESFACT",
            "ATINDICATOR02": "SALESFACT",
        },
    )

    assert '"% UNIT MARKET SHARE YOY CHANGE"' not in qualified_sql
    assert 'SALESFACT."PCT_UNIT_MARKET_SHARE_YOY_CHANGE"' in qualified_sql


def test_semantic_ddl_sanitizer_normalizes_display_name_metric_refs():
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer())

    ddl = """CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."MODEL"
TABLES (
  SALESFACT AS "DB"."SCHEMA"."SALESFACT" PRIMARY KEY ("ID")
)
DIMENSIONS (
  SALESFACT."ID" AS SALESFACT."ID"
)
METRICS (
  SALESFACT."TOTAL_UNITS_R12MS" AS CAST(NULL AS DOUBLE),
  SALESFACT."TOTAL_VANARSDEL_UNITS_R12M" AS CAST(NULL AS DOUBLE),
  SALESFACT."PCT_UNITS_MARKET_SHARE_R12M" AS CASE WHEN "Total VanArsdel Units R12M" = 0 THEN 0 ELSE "Total VanArsdel Units R12M" / NULLIF("Total Units R12Ms", 0) END,
  SALESFACT."TOTAL_UNITS_YTD" AS NULL,
  SALESFACT."TOTAL_VANARSDEL_UNITS_YTD" AS SUM(CASE WHEN PRODUCT.ISVANARSDEL = 'Yes' THEN SALESFACT."TOTAL_UNITS_YTD" ELSE 0 END::FLOAT),
  SALESFACT."PCT_UNIT_MARKET_SHARE_YOY_CHANGE" AS CAST(NULL AS DOUBLE),
  SALESFACT."ATINDICATOR02" AS CASE WHEN "salesfact"."% UNIT MARKET SHARE YOY CHANGE" < 0 THEN 1 ELSE 2 END,
  SENTIMENT."SENTIMENT" AS AVG(SENTIMENT."SCORE"),
  SALESFACT."ATINDICATOR04" AS SUM(CASE WHEN SALESFACT."SENTIMENT" < 65 THEN 1 ELSE 2 END::FLOAT)
);"""

    normalized = sanitizer.sanitize_structure(ddl)

    assert '"Total VanArsdel Units R12M"' not in normalized
    assert '"Total Units R12Ms"' not in normalized
    assert '"% UNIT MARKET SHARE YOY CHANGE"' not in normalized
    assert '"TOTAL_VANARSDEL_UNITS_R12M"' in normalized
    assert '"TOTAL_UNITS_R12MS"' in normalized
    assert 'SALESFACT."PCT_UNIT_MARKET_SHARE_YOY_CHANGE"' in normalized
    assert 'SALESFACT."SENTIMENT"' not in normalized
    assert 'SENTIMENT."SENTIMENT"' in normalized
    assert 'SALESFACT."TOTAL_VANARSDEL_UNITS_YTD" AS CAST(NULL AS DOUBLE)' in normalized


def test_semantic_ddl_sanitizer_records_null_cast_substitutions_in_drop_ledger():
    """Same defect class as the LLM-translation-layer guard: whenever this
    structural normalization pass neuters a metric expression to
    CAST(NULL AS DOUBLE) (or passes one through unchanged), that must be
    recorded via DropLedger, not just silently reflected in the DDL text --
    even though this pass is not currently wired into production, it
    shouldn't independently reintroduce the same silent-drop bug if it ever
    is."""
    from semabridge.core.drop_ledger import DropLedger, DropStage

    ledger = DropLedger()
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer(), drop_ledger=ledger)

    ddl = """CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."MODEL"
TABLES (
  SALESFACT AS "DB"."SCHEMA"."SALESFACT" PRIMARY KEY ("ID")
)
DIMENSIONS (
  SALESFACT."ID" AS SALESFACT."ID"
)
METRICS (
  SALESFACT."REAL_METRIC" AS SUM(SALESFACT."UNITS"),
  SALESFACT."TOTAL_UNITS_YTD" AS NULL
);"""

    normalized = sanitizer.sanitize_structure(ddl)

    assert 'SALESFACT."TOTAL_UNITS_YTD" AS CAST(NULL AS DOUBLE)' in normalized
    records = ledger.to_json()
    assert len(records) == 1
    assert records[0]["entity_name"] == "TOTAL_UNITS_YTD"
    assert records[0]["stage"] == DropStage.DDL_EMISSION.value
    # The real, working metric must never generate a ledger entry.
    assert not any(r["entity_name"] == "REAL_METRIC" for r in records)


def test_remediate_invalid_identifier_reports_every_metric_nulled_in_one_sweep():
    """When Snowflake rejects a shared anchor column reference qualified
    with its table alias (e.g. "SALESFACT.MAX_DATE" -- distinct from the
    bare "MAX_DATE" special case, which preserves semantics instead of
    nulling anything), remediate_invalid_identifier's general METRICS-clause
    sweep replaces every metric expression containing that token with
    CAST(NULL AS DOUBLE) in one pass. It must report every one of those
    metric names in nulled_metric_names -- the caller uses this list 1:1 to
    build DropLedger records, and used to only ever learn about one of them."""
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer())

    ddl = """CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."MODEL"
TABLES (
  SALESFACT AS "DB"."SCHEMA"."SALESFACT_ENRICHED" PRIMARY KEY ("ID")
)
DIMENSIONS (
  SALESFACT."ID" AS SALESFACT."ID"
)
METRICS (
  SALESFACT."TOTAL_UNITS_YTD" AS SUM(CASE WHEN SALESFACT."COL_DATE" <= SALESFACT."MAX_DATE" THEN SALESFACT."UNITS" ELSE NULL END),
  SALESFACT."TOTAL_UNITS_SPLY" AS SUM(CASE WHEN SALESFACT."COL_DATE" <= SALESFACT."MAX_DATE" THEN SALESFACT."UNITS" ELSE NULL END),
  SALESFACT."TOTAL_UNITS_YTD_VAR" AS SUM(CASE WHEN SALESFACT."COL_DATE" <= SALESFACT."MAX_DATE" THEN SALESFACT."UNITS" ELSE NULL END) - 1,
  SALESFACT."SALES_DOL" AS SUM(SALESFACT."REVENUE")
);"""

    fixed_ddl, changed, nulled = sanitizer.remediate_invalid_identifier(ddl, "SALESFACT.MAX_DATE")

    assert changed is True
    assert set(nulled) == {"TOTAL_UNITS_YTD", "TOTAL_UNITS_SPLY", "TOTAL_UNITS_YTD_VAR"}
    assert 'SALESFACT."TOTAL_UNITS_YTD" AS CAST(NULL AS DOUBLE)' in fixed_ddl
    assert 'SALESFACT."TOTAL_UNITS_SPLY" AS CAST(NULL AS DOUBLE)' in fixed_ddl
    assert 'SALESFACT."TOTAL_UNITS_YTD_VAR" AS CAST(NULL AS DOUBLE)' in fixed_ddl
    # A metric that never references the invalid identifier must be untouched.
    assert 'SALESFACT."SALES_DOL" AS SUM(SALESFACT."REVENUE")' in fixed_ddl


def test_remediate_invalid_identifier_bare_anchor_token_still_nulls_nothing():
    """Unchanged control case: the bare-token special case (Snowflake
    reports plain 'MAX_DATE', not table-qualified) keeps every metric's real
    semantics by substituting CURRENT_DATE() globally, so
    nulled_metric_names must stay empty -- this must not regress into
    treating the anchor substitution as a metric null."""
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer())

    ddl = """METRICS (
  SALESFACT."TOTAL_UNITS_YTD" AS SUM(CASE WHEN SALESFACT."COL_DATE" <= MAX_DATE THEN SALESFACT."UNITS" ELSE NULL END)
);"""

    fixed_ddl, changed, nulled = sanitizer.remediate_invalid_identifier(ddl, "MAX_DATE")

    assert changed is True
    assert nulled == []
    assert "CURRENT_DATE()" in fixed_ddl
    assert "CAST(NULL AS DOUBLE)" not in fixed_ddl
