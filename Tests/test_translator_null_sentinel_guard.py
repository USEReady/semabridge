"""Regression tests for the NULL-cast sentinel guard inside
MetricExpressionTranslator._try_llm_metric_fallback_expression.

Every LLM-facing prompt in this codebase instructs providers to return the
literal string ``CAST(NULL AS DOUBLE)`` when a DAX pattern is impossible to
translate. Before the fix, nothing distinguished that sentinel from a real
scalar SQL expression, so it was accepted as a "successful" translation and
silently emitted as a dead metric with zero DropLedger record. These tests
prove each of the three acceptance steps inside
_try_llm_metric_fallback_expression now rejects the sentinel (falls through
to the next step / returns None) instead of returning it as if it were real
SQL — using synthetic, placeholder-only metric/table/column names.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.dax_translation.types import TranslationResult
from semabridge.utils.identifiers import IdentifierSanitizer

_COL_LOOKUP = {"SomeTable": {"SOMECOLUMN"}}
_ALIASES = {"SomeTable": "sometable"}


def _metric(dax="SOME_UNSUPPORTED_XYZ_PATTERN(1)"):
    return SimpleNamespace(
        unique_name="Metric_A",
        name="Metric_A",
        dataset="SomeTable",
        expression=dax,
    )


class _FakeDaxTranslationService:
    """Test double standing in for DaxTranslationService.translate_metric()."""

    def __init__(self, result: TranslationResult):
        self._result = result

    def translate_metric(self, request):
        return self._result


def _translator_with_no_op_fallbacks(monkeypatch, *, tier5_result=None):
    """A translator instance whose rule-based step and DaxTranslationService
    step are both neutralized (return no candidate), so a test can isolate
    exactly one of the three acceptance steps."""
    translator = MetricExpressionTranslator(IdentifierSanitizer())

    import semabridge.converter.dax_rule_translator as rule_mod
    monkeypatch.setattr(rule_mod, "rule_based_translation", lambda dax, alias, label: None)

    fake_service = _FakeDaxTranslationService(
        tier5_result
        if tier5_result is not None
        else TranslationResult(sql=None, tier=4, original_dax="")
    )
    monkeypatch.setattr(translator, "_get_dax_translation_service", lambda: fake_service)

    return translator


def _call(translator, metric):
    return translator._try_llm_metric_fallback_expression(
        metric=metric,
        metric_name=metric.unique_name,
        table_alias="sometable",
        alias_by_raw={},
        dataset_col_lookup=_COL_LOOKUP,
        dataset_aliases=_ALIASES,
        metric_name_set=set(),
        all_physical_col_names={"SOMECOLUMN"},
        emittable_metric_name_set=set(),
        skipped_metric_names=set(),
    )


def test_rule_based_step_rejects_null_cast_sentinel(monkeypatch):
    """Step 1: if the deterministic rule engine itself ever returned the
    NULL-cast placeholder, it must not be accepted as a valid translation."""
    translator = MetricExpressionTranslator(IdentifierSanitizer())

    import semabridge.converter.dax_rule_translator as rule_mod
    monkeypatch.setattr(rule_mod, "rule_based_translation", lambda dax, alias, label: "CAST(NULL AS DOUBLE)")

    fake_service = _FakeDaxTranslationService(TranslationResult(sql=None, tier=4, original_dax=""))
    monkeypatch.setattr(translator, "_get_dax_translation_service", lambda: fake_service)

    result = _call(translator, _metric())
    assert result is None


def test_prefetch_cache_step_rejects_null_cast_sentinel(monkeypatch):
    """Step 2: the OpenAI batch-prefetch cache is populated with the
    sentinel for this metric (exactly what an LLM batch call would store
    when it declines to translate) — must not be returned as a success."""
    translator = _translator_with_no_op_fallbacks(monkeypatch)
    translator._openai_prefetch_sql_by_metric["Metric_A"] = "CAST(NULL AS DOUBLE)"

    result = _call(translator, _metric())
    assert result is None


def test_dax_translation_service_step_rejects_null_cast_sentinel(monkeypatch):
    """Step 3: DaxTranslationService (Tier 1-4 + Tier 5) returns a
    'successful' TranslationResult whose sql is the NULL-cast placeholder —
    must not be accepted just because is_success is True."""
    translator = _translator_with_no_op_fallbacks(
        monkeypatch,
        tier5_result=TranslationResult(
            sql="CAST(NULL AS DOUBLE)", tier=5, original_dax="", provider="fake"
        ),
    )

    result = _call(translator, _metric())
    assert result is None


def test_dax_translation_service_step_accepts_real_sql_unchanged(monkeypatch):
    """No false positives: a genuine scalar SQL result from
    DaxTranslationService must still be returned as-is."""
    translator = _translator_with_no_op_fallbacks(
        monkeypatch,
        tier5_result=TranslationResult(
            sql='SUM(sometable."SOMECOLUMN")', tier=5, original_dax="", provider="fake"
        ),
    )

    result = _call(translator, _metric())
    assert result == 'SUM(sometable."SOMECOLUMN")'


def test_real_sql_mentioning_null_keyword_is_not_treated_as_sentinel(monkeypatch):
    """No false positives: real SQL that merely contains the word NULL
    (e.g. an IS NULL guard) must not be mistaken for the NULL-cast
    placeholder — only an exact CAST(NULL AS <type>) shape counts."""
    translator = _translator_with_no_op_fallbacks(
        monkeypatch,
        tier5_result=TranslationResult(
            sql='CASE WHEN sometable."SOMECOLUMN" IS NULL THEN 0 ELSE sometable."SOMECOLUMN" END',
            tier=5,
            original_dax="",
            provider="fake",
        ),
    )

    result = _call(translator, _metric())
    assert result == 'CASE WHEN sometable."SOMECOLUMN" IS NULL THEN 0 ELSE sometable."SOMECOLUMN" END'
