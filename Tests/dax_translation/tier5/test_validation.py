"""Unit tests for semabridge.dax_translation.tier5.validation — synthetic
data only. Proves the verbatim-salvaged validator still behaves exactly
like connectors/translator.py's original before it's wired into any real
pipeline.
"""
from semabridge.dax_translation.tier5.validation import (
    MetricSqlValidator,
    fix_common_llm_issues,
    _is_scalar_metric_sql,
    _dax_divide_lost_its_division,
    validate_metric_column_references,
    normalize_metric_column_references,
    _to_snowflake_style_quoting,
    _from_snowflake_style_quoting,
)
from semabridge.dax_translation.types import TranslationRequest, Dialect

_COL_LOOKUP = {"SomeTable": {"SOMECOLUMN", "OTHERCOLUMN"}}
_ALIASES = {"SomeTable": "sometable"}


def _validator():
    return MetricSqlValidator()


def test_validate_accepts_known_column_reference():
    ok, err = _validator()._validate_metric_column_references(
        'sometable."SOMECOLUMN"', "Metric_A", _COL_LOOKUP, _ALIASES,
    )
    assert ok is True
    assert err is None


def test_validate_rejects_unknown_column():
    ok, err = _validator()._validate_metric_column_references(
        'sometable."NOPE"', "Metric_A", _COL_LOOKUP, _ALIASES,
    )
    assert ok is False
    assert "NOPE" in err
    assert "not found in dataset 'SomeTable'" in err


def test_validate_rejects_unknown_alias():
    """'NOPE' has no owner in any dataset — genuinely unresolvable, must
    still fail. (A column that IS uniquely owned by a real dataset, like
    SOMECOLUMN, now correctly resolves through an unknown alias — see
    test_validate_heals_unknown_alias_via_column_ownership below; that's
    the fix, not a regression here.)"""
    ok, err = _validator()._validate_metric_column_references(
        'unknownalias."NOPE"', "Metric_A", _COL_LOOKUP, _ALIASES,
    )
    assert ok is False
    assert "unknownalias" in err


def test_validate_heals_unknown_alias_via_column_ownership():
    """Regression: an alias the LLM got wrong must still resolve correctly
    when the referenced column is uniquely owned by a real dataset — this
    used to only work if the wrong alias happened to textually resemble a
    real dataset name."""
    ok, err = _validator()._validate_metric_column_references(
        'totally_bogus_alias."SOMECOLUMN"', "Metric_A", _COL_LOOKUP, _ALIASES,
    )
    assert ok is True, err


def test_validate_accepts_known_metric_reference():
    ok, err = _validator()._validate_metric_column_references(
        'sometable."SOME_OTHER_METRIC"', "Metric_A", _COL_LOOKUP, _ALIASES,
        metric_names={"SOME_OTHER_METRIC"},
    )
    assert ok is True
    assert err is None


def test_is_scalar_metric_sql_rejects_select_shapes():
    assert _is_scalar_metric_sql("SELECT 1") is False
    assert _is_scalar_metric_sql('SUM(sometable."SOMECOLUMN")') is True
    assert _is_scalar_metric_sql("") is False


def test_is_scalar_metric_sql_rejects_window_functions_for_snowflake():
    """Real gap found while investigating the ALLEXCEPT/ALL NULL bug: this
    verbatim-salvaged shape check never forbade OVER/PARTITION BY, unlike
    every other copy of this same guard (metrics_clause_builder.py,
    snowflake_metric_sql.py). A real LLM could plausibly suggest a
    window-function "fix" for an ALLEXCEPT-shaped metric — must be
    rejected for Snowflake, the default dialect."""
    windowed = 'SUM(sometable."SOMECOLUMN") OVER (PARTITION BY sometable."OTHERCOLUMN")'
    assert _is_scalar_metric_sql(windowed) is False
    assert _is_scalar_metric_sql(windowed, dialect="snowflake") is False
    assert _is_scalar_metric_sql(windowed, dialect=Dialect.SNOWFLAKE) is False


def test_is_scalar_metric_sql_allows_window_functions_for_databricks():
    """Databricks measure expressions legitimately use OVER/PARTITION BY
    (see connectors/databricks_measure_translation.py's own rolling-window
    generation) — the guard must not regress that legitimate path."""
    windowed = "SUM(`sometable`.`somecolumn`) OVER (PARTITION BY `sometable`.`othercolumn`)"
    assert _is_scalar_metric_sql(windowed, dialect="databricks") is True
    assert _is_scalar_metric_sql(windowed, dialect=Dialect.DATABRICKS) is True


def test_dax_divide_lost_its_division_detects_missing_slash():
    assert _dax_divide_lost_its_division("DIVIDE([Measure_A],[Measure_B])", 'SUM(sometable."SOMECOLUMN")') is True
    assert _dax_divide_lost_its_division("DIVIDE([Measure_A],[Measure_B])", "a / b") is False
    assert _dax_divide_lost_its_division("SUM([Measure_A])", 'SUM(sometable."SOMECOLUMN")') is False


def test_fix_common_llm_issues_no_longer_force_rewrites_misspelled_date_alias():
    """Regression: fix_common_llm_issues used to hardcode any CALENDAR/
    DATES/DATE alias to a literal 'COL_DATE' target — wrong for any
    customer whose real date-table alias isn't spelled that way. It must
    leave the alias untouched now; the real resolution happens
    structurally downstream via _heal_unknown_alias, keyed on which
    dataset actually owns the referenced column, not a hardcoded name."""
    assert fix_common_llm_issues('CALENDAR."THE_YEAR_COL" > 1', "x") == 'CALENDAR."THE_YEAR_COL" > 1'
    assert fix_common_llm_issues('DATES."THE_YEAR_COL" > 1', "x") == 'DATES."THE_YEAR_COL" > 1'
    assert fix_common_llm_issues('salesfact."UNITS"', "x") == 'salesfact."UNITS"'


def test_misspelled_date_alias_still_resolves_correctly_end_to_end():
    """Even though fix_common_llm_issues no longer rewrites the alias, the
    metric still validates correctly because _heal_unknown_alias resolves
    it structurally by column ownership — using a synthetic date-like
    dataset name that shares nothing with any real project schema."""
    col_lookup = {"CalendarPlaceholderDataset": {"THE_YEAR_COL"}}
    aliases = {"CalendarPlaceholderDataset": "cal_tbl"}
    sql = 'DATES."THE_YEAR_COL" > 1'  # LLM used the wrong alias spelling
    repaired = fix_common_llm_issues(sql, "x")
    ok, err = _validator()._validate_metric_column_references(
        repaired, "Metric_A", col_lookup, aliases,
    )
    assert ok is True, err


def test_module_level_wrappers_use_request_fields():
    request = TranslationRequest(
        dax="SUM(SomeColumn)",
        dataset_name="SomeTable",
        table_alias="sometable",
        dataset_col_lookup=_COL_LOOKUP,
        dataset_aliases=_ALIASES,
        metric_name="Metric_A",
    )
    ok, err = validate_metric_column_references('sometable."SOMECOLUMN"', request)
    assert ok is True
    assert err is None

    ok, err = validate_metric_column_references('sometable."NOPE"', request)
    assert ok is False

    normalized = normalize_metric_column_references("sometable.othercolumn", request)
    assert normalized == 'sometable.OTHERCOLUMN'


# ---------------------------------------------------------------------------
# Databricks dialect — quote-translation sandwich (Step 4 / Pipeline C fix).
# The verbatim-salvaged regexes only recognize Snowflake double-quoting;
# empirically, backtick-quoted Databricks SQL referencing a nonexistent
# column was silently ACCEPTED (invisible to the checks, not validated and
# passed) before this fix. These tests pin that gap closed.
# ---------------------------------------------------------------------------

_DBX_COL_LOOKUP = {"sales": {"REVENUE", "COST"}}
_DBX_ALIASES = {"sales": "sales"}


def _dbx_request(**overrides):
    defaults = dict(
        dax="SUM('Sales'[Revenue])",
        dataset_name="sales",
        table_alias="sales",
        dataset_col_lookup=_DBX_COL_LOOKUP,
        dataset_aliases=_DBX_ALIASES,
        dialect=Dialect.DATABRICKS,
        metric_name="Metric_A",
    )
    defaults.update(overrides)
    return TranslationRequest(**defaults)


def test_databricks_backtick_quoted_valid_column_is_accepted():
    ok, err = validate_metric_column_references("SUM(`sales`.`REVENUE`)", _dbx_request())
    assert ok is True
    assert err is None


def test_databricks_backtick_quoted_nonexistent_column_is_rejected():
    """The exact bug found during Step 4 verification: before the fix this
    was silently accepted because backtick-quoted references never matched
    the (Snowflake-only) validator patterns at all."""
    ok, err = validate_metric_column_references(
        "SUM(`sales`.`NOPE_DOES_NOT_EXIST`)", _dbx_request()
    )
    assert ok is False
    assert "NOPE_DOES_NOT_EXIST" in err


def test_databricks_normalize_never_leaks_snowflake_double_quotes():
    normalized = normalize_metric_column_references("SUM(`sales`.`REVENUE`)", _dbx_request())
    assert '"' not in normalized


def test_snowflake_validation_unaffected_by_databricks_quote_handling():
    """Regression guard: the Snowflake path must behave identically to
    before this fix — quote-translation is a no-op for dialect=snowflake."""
    sf_request = TranslationRequest(
        dax="SUM('Sales'[Revenue])",
        dataset_name="sales",
        table_alias="sales",
        dataset_col_lookup=_DBX_COL_LOOKUP,
        dataset_aliases=_DBX_ALIASES,
        dialect=Dialect.SNOWFLAKE,
        metric_name="Metric_A",
    )
    ok, _ = validate_metric_column_references('sales."REVENUE"', sf_request)
    assert ok is True
    ok, err = validate_metric_column_references('sales."NOPE"', sf_request)
    assert ok is False
    assert "NOPE" in err


def test_quote_translation_round_trip_helpers():
    internal = _to_snowflake_style_quoting("SUM(`sales`.`REVENUE`)", "databricks")
    assert internal == 'SUM(sales."REVENUE")'
    assert _from_snowflake_style_quoting(internal, "databricks") == "SUM(`sales`.`REVENUE`)"
    # a standalone bare backtick-quoted metric reference round-trips too
    assert _from_snowflake_style_quoting('"SOME_METRIC"', "databricks") == "`SOME_METRIC`"
    # true no-op only when there is nothing to convert
    assert _to_snowflake_style_quoting('SUM(x."y")', "snowflake") == 'SUM(x."y")'
    assert _from_snowflake_style_quoting('SUM(x."y")', "snowflake") == 'SUM(x."y")'


def test_to_snowflake_style_quoting_normalizes_backticks_even_for_snowflake_dialect():
    """Backticks are never valid Snowflake syntax, so their presence — not
    the declared dialect — must trigger normalization; a Snowflake request
    whose LLM output backtick-quotes identifiers anyway (e.g. confused by
    the prompt's Databricks-flavored few-shot examples, see item 18) used
    to be invisible to the validator, the exact blindness this sandwich was
    built to close for Databricks. _from_snowflake_style_quoting is NOT
    widened the same way: converting the validated result back out must
    still follow the real declared dialect, not whatever quoting the input
    happened to arrive in."""
    assert _to_snowflake_style_quoting("SUM(`x`.`y`)", "snowflake") == 'SUM(x."y")'
    assert _from_snowflake_style_quoting('SUM(x."y")', "snowflake") == 'SUM(x."y")'


def test_snowflake_validation_catches_backtick_quoted_nonexistent_column():
    """The item-18 gap this closes: before, a Snowflake-dialect response
    with backtick-quoted identifiers was silently accepted regardless of
    whether the referenced column existed at all."""
    sf_request = TranslationRequest(
        dax="SUM('Sales'[Revenue])",
        dataset_name="sales",
        table_alias="sales",
        dataset_col_lookup=_DBX_COL_LOOKUP,
        dataset_aliases=_DBX_ALIASES,
        dialect=Dialect.SNOWFLAKE,
        metric_name="Metric_A",
    )
    ok, err = validate_metric_column_references("SUM(`sales`.`NOPE_DOES_NOT_EXIST`)", sf_request)
    assert ok is False
    assert "NOPE_DOES_NOT_EXIST" in err


def test_build_safe_sum_sql_omits_snowflake_only_syntax_for_databricks():
    """::FLOAT casts and IFF(...) flag-wrapping are Snowflake-only syntax —
    invalid Databricks/Spark SQL. Verified they're gated by dialect."""
    v = MetricSqlValidator()
    assert v._build_safe_sum_sql("sales.REVENUE", "REVENUE", dialect="snowflake") == "SUM(sales.REVENUE::FLOAT)"
    assert v._build_safe_sum_sql("sales.REVENUE", "REVENUE", dialect="databricks") == "SUM(sales.REVENUE)"
    # even for a flag-like column name that would trigger IFF(...) wrapping on Snowflake
    assert v._build_safe_sum_sql("sales.IS_ACTIVE", "IS_ACTIVE", dialect="databricks") == "SUM(sales.IS_ACTIVE)"


# ---------------------------------------------------------------------------
# Regression tests: alias/column resolution must be structural (schema-
# driven), never guessed from names — synthetic placeholder data only,
# sharing nothing with any real project's dataset/column/table names.
# ---------------------------------------------------------------------------

def test_heal_unknown_alias_resolves_by_column_ownership():
    """An alias the LLM got completely wrong must still resolve correctly
    when the referenced column is uniquely owned by one real dataset —
    resolved by checking dataset_col_lookup, never by pattern-matching the
    (already wrong) alias's spelling."""
    col_lookup = {"WidgetFacts": {"WIDGET_COUNT"}, "GadgetFacts": {"GADGET_COUNT"}}
    aliases = {"WidgetFacts": "wf", "GadgetFacts": "gf"}
    resolved = _validator()._heal_unknown_alias("bogus_alias", "WIDGET_COUNT", aliases, col_lookup)
    assert resolved == "WidgetFacts"


def test_heal_unknown_alias_returns_none_when_column_is_ambiguous():
    """Two synthetic datasets both declare the same column name — with no
    relationship graph available, this must decline (None) rather than
    guess, since neither name resembles the bogus alias either."""
    col_lookup = {"WidgetFacts": {"SHARED_COL"}, "GadgetFacts": {"SHARED_COL"}}
    aliases = {"WidgetFacts": "wf", "GadgetFacts": "gf"}
    resolved = _validator()._heal_unknown_alias("bogus_alias", "SHARED_COL", aliases, col_lookup)
    assert resolved is None


def test_qualify_bare_column_identifiers_leaves_ambiguous_column_unqualified():
    """Two synthetic datasets — neither named anything like 'FACT' — both
    declare the same column. With the old code, this only resolved when
    exactly one candidate's dataset name happened to contain 'FACT'; now
    it must fail closed (leave the bare token as-is) instead of guessing."""
    col_lookup = {"AlphaWidgets": {"SHARED_METRIC_COL"}, "BetaGadgets": {"SHARED_METRIC_COL"}}
    aliases = {"AlphaWidgets": "alpha", "BetaGadgets": "beta"}
    sql = "SUM(SHARED_METRIC_COL)"
    result = _validator()._qualify_bare_column_identifiers(sql, col_lookup, aliases)
    assert result == sql  # left unqualified, not silently assigned to either dataset


def test_repair_bare_aggregate_identifiers_coerces_ambiguous_column_to_null():
    """Same ambiguous-ownership scenario as above, through the aggregate-
    repair path — must coerce to NULL (safe, visible) rather than guess."""
    col_lookup = {"AlphaWidgets": {"SHARED_METRIC_COL"}, "BetaGadgets": {"SHARED_METRIC_COL"}}
    aliases = {"AlphaWidgets": "alpha", "BetaGadgets": "beta"}
    sql = "SUM(SHARED_METRIC_COL)"
    result = _validator()._repair_bare_aggregate_identifiers(sql, "Some_Metric", col_lookup, aliases)
    assert result == "NULL"


def test_pick_preferred_aggregate_column_returns_none_when_ambiguous():
    """Two non-key/non-date candidate columns with no keyword relationship
    to the metric name at all — must decline rather than guess via
    keyword-overlap scoring."""
    columns = {"WIDGET_TOTAL", "GADGET_TOTAL"}
    result = _validator()._pick_preferred_aggregate_column("SomeUnrelatedMetricName", columns)
    assert result is None


def test_pick_preferred_aggregate_column_returns_sole_non_key_candidate():
    """Excluding key/FK/date-suffixed columns leaves exactly one candidate
    — a real structural signal, safe to auto-resolve."""
    columns = {"WIDGET_TOTAL", "WIDGET_ID", "WIDGET_KEY", "WIDGET_DATE"}
    result = _validator()._pick_preferred_aggregate_column("Anything", columns)
    assert result == "WIDGET_TOTAL"
