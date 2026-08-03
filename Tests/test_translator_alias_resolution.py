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
