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


def test_relationship_builder_warns_on_many_to_many_fan_out_risk(caplog):
    """Snowflake's RELATIONSHIPS clause grammar has no cardinality/cross-
    filter keyword -- cardinality and cross_filter are captured on
    OSIRelationship/SMLRelationship and survive OSI->SML conversion, but
    relationships_clause_builder.py used to discard them silently at
    DDL-emission time with no signal anywhere that a many-to-many (or
    bidirectional) relationship can fan out rows for metrics joined across
    it. This pins the new advisory log line; the emitted DDL itself is
    unchanged either way (no way to represent the risk IN the DDL)."""
    from semabridge.sml.models import Cardinality, CrossFilterDirection

    builder = RelationshipsClauseBuilder(IdentifierSanitizer(), _Schema(), _DDL())
    rel = SimpleNamespace(
        unique_name="bridge_rel",
        is_active=True,
        from_dataset="product",
        to_dataset="customer",
        from_columns=["Product_Key"],
        to_columns=["Customer_Key"],
        cardinality=Cardinality.MANY_TO_MANY,
        cross_filter=CrossFilterDirection.SINGLE,
    )

    with caplog.at_level("WARNING"):
        lines = builder.build_for_osi(
            SimpleNamespace(relationships=[rel]),
            dataset_aliases={"product": "PRODUCT", "customer": "CUSTOMER"},
            dataset_by_name={"product": SimpleNamespace(), "customer": SimpleNamespace()},
            dataset_col_lookup={"product": {"PRODUCT_KEY"}, "customer": {"CUSTOMER_KEY"}},
            declared_pk_by_alias={},
            relationship_target_alias={},
        )

    assert len(lines) == 1
    assert "REFERENCES" in lines[0]  # DDL itself is unaffected
    assert any("fan-out" in r.message and "product" in r.message for r in caplog.records)


def test_relationship_builder_does_not_warn_on_ordinary_many_to_one(caplog):
    """Negative control for the fan-out advisory above: an ordinary
    many-to-one, single-cross-filter relationship (the common case) must
    not trigger the warning."""
    from semabridge.sml.models import Cardinality, CrossFilterDirection

    builder = RelationshipsClauseBuilder(IdentifierSanitizer(), _Schema(), _DDL())
    rel = SimpleNamespace(
        unique_name="spend_fact_date_rel",
        is_active=True,
        from_dataset="spend_fact",
        to_dataset="date",
        from_columns=["Posting_Date"],
        to_columns=["Cal_Date"],
        cardinality=Cardinality.MANY_TO_ONE,
        cross_filter=CrossFilterDirection.SINGLE,
    )

    with caplog.at_level("WARNING"):
        builder.build_for_osi(
            SimpleNamespace(relationships=[rel]),
            dataset_aliases={"spend_fact": "SPEND_FACT", "date": "COL_DATE"},
            dataset_by_name={"spend_fact": SimpleNamespace(), "date": SimpleNamespace()},
            dataset_col_lookup={"spend_fact": {"POSTING_DATE"}, "date": {"CAL_DATE"}},
            declared_pk_by_alias={},
            relationship_target_alias={},
        )

    assert not any("fan-out" in r.message for r in caplog.records)


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


# ---------------------------------------------------------------------------
# Regression coverage for the proj-09-08-test incident: Snowflake rejected
# the first DDL attempt with `invalid identifier '_'`, and
# remediate_invalid_identifier's old `invalid_norm in line_norm` substring
# check then matched almost every line in RELATIONSHIPS/DIMENSIONS/METRICS
# (since ordinary identifiers like SALESFACT_PRODUCTID_PRODUCT_PRODUCTID
# contain underscores too), wiping out the entire RELATIONSHIPS block
# instead of touching only a genuine isolated `_` token.
# ---------------------------------------------------------------------------

_REAL_MODEL_STYLE_DDL = """CREATE OR REPLACE SEMANTIC VIEW "SEMABRIDGE"."SEMABRIDGE_WORKSPACE"."PROJ0908TEST_SEMANTIC"
TABLES (
  SALESFACT AS "SEMABRIDGE"."SEMABRIDGE_WORKSPACE"."SALESFACT" PRIMARY KEY ("PRODUCTID"),
  PRODUCT AS "SEMABRIDGE"."SEMABRIDGE_WORKSPACE"."PRODUCT" PRIMARY KEY ("PRODUCTID")
)
RELATIONSHIPS (
  SALESFACT_PRODUCTID_PRODUCT_PRODUCTID AS SALESFACT ("PRODUCTID") REFERENCES PRODUCT ("PRODUCTID")
)
DIMENSIONS (
  SALESFACT."UNITS" AS SALESFACT."UNITS"
)
METRICS (
  SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT."UNITS"::FLOAT)
);"""


def test_remediate_invalid_identifier_bare_underscore_does_not_wipe_relationships():
    """A bare '_' invalid identifier that appears nowhere as a genuine
    standalone token must leave every clause -- especially RELATIONSHIPS --
    completely untouched, even though every relationship/dimension/metric
    name here contains underscores as part of normal identifiers."""
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer())

    fixed_ddl, changed, nulled = sanitizer.remediate_invalid_identifier(_REAL_MODEL_STYLE_DDL, "_")

    assert changed is False
    assert nulled == []
    assert fixed_ddl == _REAL_MODEL_STYLE_DDL


def test_remediate_invalid_identifier_bare_underscore_removes_only_the_genuine_token():
    """When a bare '_' genuinely appears as its own token, remediation must
    still find and fix it -- just without collateral damage to unrelated
    lines that merely contain underscores inside normal identifiers."""
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer())
    ddl = _REAL_MODEL_STYLE_DDL.replace(
        '  SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT."UNITS"::FLOAT)\n)',
        '  SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT."UNITS"::FLOAT),\n'
        '  SALESFACT."BROKEN_METRIC" AS _ + 1\n)',
    )

    fixed_ddl, changed, nulled = sanitizer.remediate_invalid_identifier(ddl, "_")

    assert changed is True
    assert nulled == ["BROKEN_METRIC"]
    # The real relationship, dimension, and unrelated metric all survive.
    assert (
        'SALESFACT_PRODUCTID_PRODUCT_PRODUCTID AS SALESFACT ("PRODUCTID") '
        'REFERENCES PRODUCT ("PRODUCTID")'
    ) in fixed_ddl
    assert 'SALESFACT."UNITS" AS SALESFACT."UNITS"' in fixed_ddl
    assert 'SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT."UNITS"::FLOAT)' in fixed_ddl
    # Only the metric that genuinely referenced the bad token was nulled.
    assert 'SALESFACT."BROKEN_METRIC" AS CAST(NULL AS DOUBLE)' in fixed_ddl


def test_remediate_invalid_identifier_bare_underscore_ignores_like_wildcard_literal():
    """A `LIKE '_a%'` SQL wildcard is a string literal, not an identifier
    reference, and must not be mistaken for the bad `_` token."""
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer())
    ddl = """METRICS (
  SALESFACT."NAME_STARTS_WITH_A" AS SUM(CASE WHEN SALESFACT."NAME" LIKE '_a%' THEN 1 ELSE 0 END)
);"""

    fixed_ddl, changed, nulled = sanitizer.remediate_invalid_identifier(ddl, "_")

    assert changed is False
    assert nulled == []
    assert fixed_ddl == ddl


def test_remediate_invalid_identifier_removes_emptied_relationships_and_dimensions_clauses():
    """Defense-in-depth: if remediation empties RELATIONSHIPS or DIMENSIONS
    out entirely, the clause's header and closing paren must be removed
    too -- leaving 'RELATIONSHIPS (\\n)' behind is syntactically invalid
    Snowflake DDL. METRICS, which never referenced the bad column, is
    untouched."""
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer())
    ddl = """CREATE OR REPLACE SEMANTIC VIEW "DB"."SCHEMA"."MODEL"
TABLES (
  SALESFACT AS "DB"."SCHEMA"."SALESFACT" PRIMARY KEY ("PRODUCTID")
)
RELATIONSHIPS (
  SALESFACT_BADCOL_PRODUCT_BADCOL AS SALESFACT ("BADCOL") REFERENCES PRODUCT ("BADCOL")
)
DIMENSIONS (
  SALESFACT."BADCOL" AS SALESFACT."BADCOL"
)
METRICS (
  SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT."UNITS")
);"""

    fixed_ddl, changed, nulled = sanitizer.remediate_invalid_identifier(ddl, "BADCOL")

    assert changed is True
    assert "RELATIONSHIPS (" not in fixed_ddl
    assert "DIMENSIONS (" not in fixed_ddl
    assert 'SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT."UNITS")' in fixed_ddl


def test_remediate_invalid_identifier_cleans_up_preexisting_empty_relationships_clause():
    """The empty-clause cleanup runs unconditionally at the end of every
    remediation call, independent of whether this call's own
    invalid_identifier is what caused the clause to be empty."""
    sanitizer = SemanticDDLSanitizer(IdentifierSanitizer())
    ddl = """RELATIONSHIPS (
)
METRICS (
  SALESFACT."BROKEN" AS UNRELATED_BAD_TOKEN
);"""

    fixed_ddl, changed, nulled = sanitizer.remediate_invalid_identifier(ddl, "UNRELATED_BAD_TOKEN")

    assert "RELATIONSHIPS (" not in fixed_ddl


# ---------------------------------------------------------------------------
# safe_table_name_static: confirmed-not-the-source, but a real latent bug.
# Traced every real caller in the codebase (identifier_utilities.py's only
# consumer is measure_sync.generate_semantic_view_tiered, which always
# prefixes the result with "V_"), and ran every real table/column/
# relationship name from proj-09-08-test's actual sml/model.yaml through it
# -- none degenerate to empty, so this function did not produce the bare `_`
# that this incident's DDL was rejected for. It is fixed anyway for
# consistency with every sibling sanitizer in identifiers.py
# (sanitize_column -> "COLUMN_UNKNOWN", sanitize_alias -> "ALIAS"), all of
# which already guarantee a non-empty result, and as a guard against any
# future caller that concatenates this result with a fixed separator.
# ---------------------------------------------------------------------------

def test_safe_table_name_static_never_returns_empty_string():
    """A fully degenerate input (only characters this function strips to
    underscores, which then collapse and get stripped) must fall back to a
    non-empty placeholder instead of "" -- matching sanitize_column's
    COLUMN_UNKNOWN and sanitize_alias's ALIAS conventions."""
    from semabridge.connectors.snowflake_emitter_parts.identifier_utilities import (
        safe_table_name_static,
    )

    assert safe_table_name_static("") == "UNKNOWN"
    assert safe_table_name_static("   ") == "UNKNOWN"
    assert safe_table_name_static("___") == "UNKNOWN"
    assert safe_table_name_static("!!!") == "UNKNOWN"
    assert safe_table_name_static("---") == "UNKNOWN"


def test_safe_table_name_static_normal_names_unaffected():
    """The fallback must not change behavior for any ordinary name."""
    from semabridge.connectors.snowflake_emitter_parts.identifier_utilities import (
        safe_table_name_static,
    )

    assert safe_table_name_static("SalesFact") == "SALESFACT"
    assert safe_table_name_static("Sales Fact Model") == "SALES_FACT_MODEL"
    assert safe_table_name_static("proj-09-08-test") == "PROJ_09_08_TEST"


def test_safe_table_name_static_real_proj_09_08_test_names_never_degenerate():
    """Every real dataset name from proj-09-08-test's actual model.yaml,
    run through the real sanitization chain, resolves to a well-formed
    non-empty identifier -- confirming this function was not the source of
    this incident's bare `_`."""
    from semabridge.connectors.snowflake_emitter_parts.identifier_utilities import (
        safe_table_name_static,
    )

    real_dataset_names = [
        "Category", "Date", "Geo", "Indicators", "KPI",
        "Manufacturer", "Product", "SalesFact", "Sentiment",
        "proj-09-08-test",
    ]
    for name in real_dataset_names:
        result = safe_table_name_static(name)
        assert result, f"{name!r} degenerated to empty"
        assert result != "_"
