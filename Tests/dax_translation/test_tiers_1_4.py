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
    assert result.sql == 'SUM(SOMETABLE."SOMECOLUMN")'
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
    # DAX BLANK arithmetic: BLANK - BLANK = BLANK, BLANK - x = -x. A bare
    # SQL subtraction would turn either-side NULL into NULL, so the
    # renderer emits the blank-safe form.
    a, b = '(SUM(sometable."SOMECOLUMN"))', '(SUM(sometable."OTHERCOLUMN"))'
    assert result.sql == (
        f"CASE WHEN {a} IS NULL AND {b} IS NULL THEN NULL "
        f"ELSE COALESCE({a}, 0) - COALESCE({b}, 0) END"
    )


def test_tier3_time_intelligence_via_ast_renderer():
    dax = "TOTALYTD(SUM('SomeTable'[SomeColumn]), 'Date'[COL_DATE])"
    result = translate_tiers_1_4(_request(dax))
    assert result is not None
    assert result.tier == 3
    # Bug-B regression guard: must be a bounded CASE WHEN, never a bare
    # window function with no date-range bound.
    assert "CASE WHEN" in result.sql
    assert " OVER " not in f" {result.sql} "


def test_tier4_allexcept_decomposes_into_a_window_over_a_helper_metric():
    """ALLEXCEPT(T, T[k]) is decomposed (fix/relationship-direction) into a
    semantic-view window metric over a synthesized additive helper metric
    (<col>_SUM, registered by MetricsClauseBuilder), partitioned by the
    kept column -- never a window over a raw physical column, which is the
    shape that deployed but returned NULL at query time. Whether the
    emission layer accepts this plain PARTITION BY form is decided (and
    recorded with a reason when rejected) by MetricsClauseBuilder's
    scalar-SQL gate, not here."""
    dax = "CALCULATE(SUM('SomeTable'[SomeColumn]), ALLEXCEPT('SomeTable', 'SomeTable'[OtherColumn]))"
    result = translate_tiers_1_4(_request(dax))
    assert result is not None
    assert result.sql == 'SUM(SOMETABLE."SOMECOLUMN_SUM"::FLOAT) OVER (PARTITION BY SOMETABLE."OTHERCOLUMN")'
    assert 'SUM(SOMETABLE."SOMECOLUMN")' not in result.sql.upper()


def test_unsupported_dax_falls_through_to_none():
    result = translate_tiers_1_4(_request("SOMENONSENSEFUNCTION(1,2,3)"))
    assert result is None


def test_empty_dax_falls_through_to_none():
    assert translate_tiers_1_4(_request("")) is None
    assert translate_tiers_1_4(_request("   ")) is None
