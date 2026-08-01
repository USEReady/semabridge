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
    ok, err = _validator()._validate_metric_column_references(
        'unknownalias."SOMECOLUMN"', "Metric_A", _COL_LOOKUP, _ALIASES,
    )
    assert ok is False
    assert "unknownalias" in err


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


def test_fix_common_llm_issues_remaps_calendar_alias():
    assert fix_common_llm_issues('CALENDAR."COL_DATE" > 1', "x") == 'COL_DATE."COL_DATE" > 1'


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
    # no-op for Snowflake dialect
    assert _to_snowflake_style_quoting("SUM(`x`.`y`)", "snowflake") == "SUM(`x`.`y`)"
    assert _from_snowflake_style_quoting('SUM(x."y")', "snowflake") == 'SUM(x."y")'


def test_build_safe_sum_sql_omits_snowflake_only_syntax_for_databricks():
    """::FLOAT casts and IFF(...) flag-wrapping are Snowflake-only syntax —
    invalid Databricks/Spark SQL. Verified they're gated by dialect."""
    v = MetricSqlValidator()
    assert v._build_safe_sum_sql("sales.REVENUE", "REVENUE", dialect="snowflake") == "SUM(sales.REVENUE::FLOAT)"
    assert v._build_safe_sum_sql("sales.REVENUE", "REVENUE", dialect="databricks") == "SUM(sales.REVENUE)"
    # even for a flag-like column name that would trigger IFF(...) wrapping on Snowflake
    assert v._build_safe_sum_sql("sales.IS_ACTIVE", "IS_ACTIVE", dialect="databricks") == "SUM(sales.IS_ACTIVE)"
