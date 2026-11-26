"""Phase 2e (e1): DAXTranslator follows the target platform's rules.

With rules for another platform the translator spells SQL in that dialect,
skips the Snowflake-only shortcut tiers (so the shared tree renderer is the
single source of truth), words "not applicable" reasons with that
platform's policies and asks Tier 5 for that dialect. Without rules it is
unchanged (Snowflake), which Tests/golden proves byte for byte.
"""

from __future__ import annotations

import dataclasses

import pytest

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.target_rules import SNOWFLAKE_RULES, DecisionRule, ReasonCode, TargetRules
from semabridge.target_rules.snowflake.dialect import SnowflakeDialect


class _BacktickDialect(SnowflakeDialect):
    # "databricks" so Tier 5's dialect enum accepts it; spelling is test-only.
    name = "databricks"
    identifier_quote = "`"
    float_cast_suffix = "::DOUBLE"

    def quote_identifier(self, name: str) -> str:
        return f"`{name}`"


def _other_rules() -> TargetRules:
    decisions = dict(SNOWFLAKE_RULES.policies.rules)
    decisions[ReasonCode.TEXT_RESULT] = DecisionRule(keep=False, text="not applicable: other platform text result")
    return TargetRules(
        target="other-test-platform",
        policies=dataclasses.replace(SNOWFLAKE_RULES.policies, rules=decisions),
        capabilities=SNOWFLAKE_RULES.capabilities,
        dialect=_BacktickDialect(),
    )


def _translate(dax: str, **kwargs):
    return DAXTranslator().translate(dax, "SALES", "Sales", metric_name="M", skip_tier5=True, **kwargs)


def test_default_is_snowflake() -> None:
    assert _translate("SUM(Sales[Amount])").sql == _translate("SUM(Sales[Amount])", rules=SNOWFLAKE_RULES).sql
    assert '"' in _translate("SUM(Sales[Amount])").sql


def test_other_platform_sql_comes_from_the_tree_renderer_in_its_dialect() -> None:
    result = _translate("SUM(Sales[Amount])", rules=_other_rules())
    assert result.sql == "SUM(SALES.`AMOUNT`::DOUBLE)"


def test_shortcut_tiers_only_run_for_snowflake(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    original = DAXTranslator._try_tier1

    def spy(self, dax, alias):
        calls.append(self._shortcut_tiers_enabled())
        return original(self, dax, alias)

    monkeypatch.setattr(DAXTranslator, "_try_tier1", spy)
    _translate("SUM(Sales[Amount])", rules=_other_rules())
    assert calls and not any(calls)  # called, but disabled for the other platform
    calls.clear()
    _translate("SUM(Sales[Amount])")
    assert calls and all(calls)


def test_not_applicable_wording_follows_the_rules() -> None:
    text = '"Sales: " & FORMAT(SUM(Sales[Amount]), "0")'
    assert _translate(text).failure_reason == "not applicable: text result"
    assert _translate(text, rules=_other_rules()).failure_reason == "not applicable: other platform text result"


def test_rules_are_restored_after_the_call() -> None:
    translator = DAXTranslator()
    translator.translate("SUM(Sales[Amount])", "SALES", "Sales", skip_tier5=True, rules=_other_rules())
    assert translator._rules is SNOWFLAKE_RULES


def test_tier5_is_asked_for_the_platform_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    class FakeTier5:
        def translate(self, request):
            seen.append(request.dialect.value)
            return None

        def translate_batch(self, requests):
            seen.extend(r.dialect.value for r in requests)
            return [None] * len(requests)

    translator = DAXTranslator()
    monkeypatch.setattr(translator, "_get_tier5_service", lambda: FakeTier5())
    dax = "SUMX(TOPN(3, Sales, Sales[Amount]), Sales[Amount])"  # declines deterministically
    translator.translate(dax, "SALES", "Sales", metric_name="M", rules=_other_rules())
    translator.batch_translate_tier5([("M", dax, "SALES", "Sales")], rules=_other_rules())
    translator.batch_translate_tier5([("M", dax, "SALES", "Sales")])
    assert "databricks" in seen and seen[-1] == "snowflake"


def test_reason_prefix_convention() -> None:
    """The translator treats a renderer/classifier message starting with
    'not applicable' or the by-design prefix as a decline. Every platform's
    reason texts must keep that convention."""
    policies = SNOWFLAKE_RULES.policies
    for code in ReasonCode:
        reason = policies.decide(code, "x").reason
        assert reason.lower().startswith(("not applicable", policies.by_design_prefix.lower())), code


def test_window_and_helper_constructs_follow_capabilities() -> None:
    """A whole-table total renders as a Snowflake enriched-view helper column;
    a platform without that capability (or without window metrics) declines
    the measure instead of emitting Snowflake-only SQL."""
    from semabridge.converter.dax_ast_parser import try_ast_translate

    dax = 'CALCULATE(COUNTA(Store[StoreType]), FILTER(ALL(Store), Store[StoreType] = "New Store"))'
    assert try_ast_translate(dax, table_alias="STORE", primary_table_name="Store") == (
        'MAX(STORE."_ALL_COUNTA_STORETYPE_NEW_STORE")'
    )
    without = dataclasses.replace(
        SNOWFLAKE_RULES,
        target="no-window-test",
        capabilities=dataclasses.replace(
            SNOWFLAKE_RULES.capabilities, window_metrics=False, enriched_helper_columns=False
        ),
    )
    errors: list = []
    assert try_ast_translate(dax, table_alias="STORE", primary_table_name="Store", rules=without, render_errors=errors) is None
    assert errors == ["not applicable: needs window metric"]
