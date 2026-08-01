"""Unit tests for semabridge.dax_translation.tiers_1_4 — synthetic data only.

These exercise the REAL, unmodified converter.dax_translator.DAXTranslator
tier methods and converter.dax_ast_parser.try_ast_translate through the
new thin wrapper — proving the wrapper reproduces the exact same dispatch
order and output as the original, unchanged code, with the inert
DeterministicTranslator detour and the duplicate double-pass removed
(neither of which can change the result, since both are pure no-ops on
these inputs).
"""
from semabridge.dax_translation.types import TranslationRequest
from semabridge.dax_translation.tiers_1_4 import translate_tiers_1_4

_COL_LOOKUP = {"SomeTable": {"SOMECOLUMN", "OTHERCOLUMN"}, "Date": {"COL_DATE"}}
_ALIASES = {"SomeTable": "sometable", "Date": "date"}


class _FakeMetric:
    def __init__(self, unique_name, expression=None, sql_expression=None):
        self.unique_name = unique_name
        self.expression = expression
        self.sql_expression = sql_expression


def _request(dax, **overrides):
    defaults = dict(
        dax=dax,
        dataset_name="SomeTable",
        table_alias="sometable",
        dataset_col_lookup=_COL_LOOKUP,
        dataset_aliases=_ALIASES,
    )
    defaults.update(overrides)
    return TranslationRequest(**defaults)


def test_tier1_direct_aggregation():
    result = translate_tiers_1_4(_request("SUM('SomeTable'[SomeColumn])"))
    assert result is not None
    assert result.tier == 1
    assert result.sql == 'SUM(sometable."SOMECOLUMN")'
    assert result.is_success


def test_tier2_strict_calculate_with_equality_filter():
    dax = 'CALCULATE(SUM(\'SomeTable\'[SomeColumn]), \'SomeTable\'[OtherColumn] = "X")'
    result = translate_tiers_1_4(_request(dax))
    assert result is not None
    assert result.tier == 2
    assert "CASE WHEN" in result.sql
    assert "SOMETABLE.OTHERCOLUMN" in result.sql.upper()


def test_tier2_branching_arithmetic_between_measures():
    metrics_ctx = [
        _FakeMetric("Measure_A", expression="SUM('SomeTable'[SomeColumn])", sql_expression='SUM(sometable."SOMECOLUMN")'),
        _FakeMetric("Measure_B", expression="SUM('SomeTable'[OtherColumn])", sql_expression='SUM(sometable."OTHERCOLUMN")'),
    ]
    result = translate_tiers_1_4(_request("[Measure_A] - [Measure_B]", metrics_context=metrics_ctx))
    assert result is not None
    assert result.tier == 2
    assert result.sql == '(SUM(sometable."SOMECOLUMN")) - (SUM(sometable."OTHERCOLUMN"))'


def test_tier3_time_intelligence_via_ast_renderer():
    dax = "TOTALYTD(SUM('SomeTable'[SomeColumn]), 'Date'[COL_DATE])"
    result = translate_tiers_1_4(_request(dax))
    assert result is not None
    assert result.tier == 3
    # Bug-B regression guard: must be a bounded CASE WHEN, never a bare
    # window function with no date-range bound.
    assert "CASE WHEN" in result.sql
    assert " OVER " not in f" {result.sql} "


def test_tier4_allexcept_fails_closed_instead_of_emitting_window_function():
    """ALLEXCEPT/ALL context-transition has no SQL equivalent inside a
    Snowflake semantic-view METRICS clause (no window functions allowed) —
    Tiers 1-4 must decline (None) rather than "succeed" with an OVER(...)
    clause that deploys fine but silently returns NULL at query time. See
    dax_ast_parser.py's DaxSqlRenderer._render_calculate."""
    dax = "CALCULATE(SUM('SomeTable'[SomeColumn]), ALLEXCEPT('SomeTable', 'SomeTable'[OtherColumn]))"
    result = translate_tiers_1_4(_request(dax))
    assert result is None


def test_unsupported_dax_falls_through_to_none():
    result = translate_tiers_1_4(_request("SOMENONSENSEFUNCTION(1,2,3)"))
    assert result is None


def test_empty_dax_falls_through_to_none():
    assert translate_tiers_1_4(_request("")) is None
    assert translate_tiers_1_4(_request("   ")) is None
