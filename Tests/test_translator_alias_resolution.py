"""Regression tests for connectors/translator.py's MetricExpressionTranslator
alias/column resolution — the same fixes applied to
dax_translation/tier5/validation.py's verbatim-salvaged copy (this class is
the original source of that salvage), proven independently since the two
copies are separate, duplicated code paths that could regress independently.

Synthetic placeholder data only — none of these names share anything with
any real project's dataset/column/table names.
"""
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer

_COL_LOOKUP = {"SomeTable": {"SOMECOLUMN", "OTHERCOLUMN"}}
_ALIASES = {"SomeTable": "sometable"}


def _translator() -> MetricExpressionTranslator:
    return MetricExpressionTranslator(identifier_sanitizer=IdentifierSanitizer(force_uppercase=True))


def test_fix_common_llm_issues_no_longer_force_rewrites_misspelled_date_alias():
    t = _translator()
    assert t.fix_common_llm_issues('CALENDAR."THE_YEAR_COL" > 1', "x") == 'CALENDAR."THE_YEAR_COL" > 1'
    assert t.fix_common_llm_issues('DATES."THE_YEAR_COL" > 1', "x") == 'DATES."THE_YEAR_COL" > 1'
    assert t.fix_common_llm_issues('salesfact."UNITS"', "x") == 'salesfact."UNITS"'


def test_heal_unknown_alias_resolves_by_column_ownership():
    t = _translator()
    col_lookup = {"WidgetFacts": {"WIDGET_COUNT"}, "GadgetFacts": {"GADGET_COUNT"}}
    aliases = {"WidgetFacts": "wf", "GadgetFacts": "gf"}
    resolved = t._heal_unknown_alias("bogus_alias", "WIDGET_COUNT", aliases, col_lookup)
    assert resolved == "WidgetFacts"


def test_heal_unknown_alias_returns_none_when_column_is_ambiguous():
    t = _translator()
    col_lookup = {"WidgetFacts": {"SHARED_COL"}, "GadgetFacts": {"SHARED_COL"}}
    aliases = {"WidgetFacts": "wf", "GadgetFacts": "gf"}
    resolved = t._heal_unknown_alias("bogus_alias", "SHARED_COL", aliases, col_lookup)
    assert resolved is None


def test_validate_heals_unknown_alias_via_column_ownership():
    t = _translator()
    ok, err = t._validate_metric_column_references(
        'totally_bogus_alias."SOMECOLUMN"', "Metric_A", _COL_LOOKUP, _ALIASES,
    )
    assert ok is True, err


def test_qualify_bare_column_identifiers_leaves_ambiguous_column_unqualified():
    """Two synthetic datasets — neither named anything like 'FACT' — both
    declare the same column. Must fail closed (leave the bare token as-is)
    instead of guessing via the old 'FACT'-substring tie-break."""
    t = _translator()
    col_lookup = {"AlphaWidgets": {"SHARED_METRIC_COL"}, "BetaGadgets": {"SHARED_METRIC_COL"}}
    aliases = {"AlphaWidgets": "alpha", "BetaGadgets": "beta"}
    sql = "SUM(SHARED_METRIC_COL)"
    result = t._qualify_bare_column_identifiers(sql, col_lookup, aliases)
    assert result == sql


def test_repair_bare_aggregate_identifiers_coerces_ambiguous_column_to_null():
    t = _translator()
    col_lookup = {"AlphaWidgets": {"SHARED_METRIC_COL"}, "BetaGadgets": {"SHARED_METRIC_COL"}}
    aliases = {"AlphaWidgets": "alpha", "BetaGadgets": "beta"}
    sql = "SUM(SHARED_METRIC_COL)"
    result = t._repair_bare_aggregate_identifiers(sql, "Some_Metric", col_lookup, aliases)
    assert result == "NULL"


def test_pick_preferred_aggregate_column_returns_none_when_ambiguous():
    t = _translator()
    columns = {"WIDGET_TOTAL", "GADGET_TOTAL"}
    result = t._pick_preferred_aggregate_column("SomeUnrelatedMetricName", columns)
    assert result is None


def test_pick_preferred_aggregate_column_returns_sole_non_key_candidate():
    t = _translator()
    columns = {"WIDGET_TOTAL", "WIDGET_ID", "WIDGET_KEY", "WIDGET_DATE"}
    result = t._pick_preferred_aggregate_column("Anything", columns)
    assert result == "WIDGET_TOTAL"


def test_normalize_metric_column_references_prefers_dax_hinted_table_over_wrong_alias():
    """Regression for a real deploy failure: a Tier-5-translated metric's SQL
    already looked "valid" by every existing check — 'selector' is a real
    alias, "YEAR_FLAG" is a real column there too — but the original DAX
    explicitly qualified that same column against 'CalendarTable', a
    DIFFERENT table that also happens to physically have it. Snowflake later
    rejected the deployed metric with "cannot refer to another dimension
    from an unrelated entity" because the metric's own dataset (Selector)
    had no relationship path to reach whatever the wrong alias implied.
    Structurally confirmed (the hinted table really does have the column),
    not a blind string swap."""
    t = _translator()
    col_lookup = {
        "SelectorTable": {"MODE", "YEAR_FLAG"},
        "CalendarTable": {"CAL_DATE", "YEAR_FLAG"},
        "FactTable": {"AMOUNT"},
    }
    aliases = {"SelectorTable": "selector", "CalendarTable": "cal", "FactTable": "fact"}
    dax = "IF(SUM('SelectorTable'[MODE])=3,CALCULATE([SomeMeasure],'CalendarTable'[Year Flag]=1))"
    sql = 'CASE WHEN SUM(selector."MODE") = 3 THEN SUM(CASE WHEN selector."YEAR_FLAG" = 1 THEN fact."AMOUNT" ELSE NULL END) END'

    normalized = t._normalize_metric_column_references(
        sql, "Metric_A", col_lookup, aliases, preferred_table_alias="selector", original_dax=dax,
    )

    assert 'cal.YEAR_FLAG' in normalized
    assert 'selector.YEAR_FLAG' not in normalized


def test_normalize_metric_column_references_ignores_dax_hint_without_bracket_refs():
    """Negative control: no 'Table'[Column] reference anywhere in the DAX ->
    dax_table_hints is empty -> behavior is identical to omitting
    original_dax entirely (the overwhelming majority of existing callers)."""
    t = _translator()
    col_lookup = {"SelectorTable": {"MODE", "YEAR_FLAG"}, "CalendarTable": {"CAL_DATE", "YEAR_FLAG"}}
    aliases = {"SelectorTable": "selector", "CalendarTable": "cal"}
    sql = 'selector."YEAR_FLAG"'

    with_dax = t._normalize_metric_column_references(
        sql, "Metric_A", col_lookup, aliases, original_dax="SUM([SomeColumn])",
    )
    without_dax = t._normalize_metric_column_references(sql, "Metric_A", col_lookup, aliases)

    # Quote-stripping for a plain (non-reserved, no-"$") column name happens
    # regardless of original_dax -- that's pre-existing _format_metric_ref
    # behavior, not something this test is about. The point here is that
    # passing an original_dax with no bracket references changes nothing
    # relative to not passing one at all, and the alias stays "selector"
    # either way (no spurious override).
    assert with_dax == without_dax == 'selector.YEAR_FLAG'


def test_normalize_metric_column_references_ignores_hint_the_hinted_table_cant_confirm():
    """Negative control: the DAX names a table for this column, but that
    table doesn't structurally have it (dataset_col_lookup disagrees) --
    e.g. it's a genuinely SelectorTable-only column and the hint is stale/
    wrong. Must NOT override in that case; only a hint confirmed by the
    real schema is trusted (same standard _heal_unknown_alias already
    holds itself to elsewhere in this file)."""
    t = _translator()
    col_lookup = {"SelectorTable": {"MODE", "YEAR_FLAG"}, "CalendarTable": {"CAL_DATE"}}
    aliases = {"SelectorTable": "selector", "CalendarTable": "cal"}
    dax = "CALCULATE([SomeMeasure],'CalendarTable'[Year Flag]=1)"
    sql = 'selector."YEAR_FLAG"'

    normalized = t._normalize_metric_column_references(
        sql, "Metric_A", col_lookup, aliases, original_dax=dax,
    )

    assert normalized == 'selector.YEAR_FLAG'


def test_normalize_metric_column_references_resolves_mixed_case_unquoted_column():
    """Regression: the unquoted-identifier regex's character class used to
    be [A-ZaZ0-9_$] — matching uppercase A-Z, the literal characters 'a'
    and 'Z', digits, underscore, and dollar, but NOT most lowercase
    letters. Any mixed-case column name (e.g. utils/naming.py's to_alias()
    legitimately produces lowercase aliases) was silently truncated at the
    first character outside that broken class."""
    t = _translator()
    col_lookup = {"SomeTable": {"MIXEDCASECOLUMN"}}
    aliases = {"SomeTable": "sometable"}
    sql = "SUM(sometable.MixedCaseColumn)"

    normalized = t._normalize_metric_column_references(sql, "Metric_A", col_lookup, aliases)

    assert "MIXEDCASECOLUMN" in normalized.upper()
    assert "sometable.M)" not in normalized  # old bug: truncated to just "M"
