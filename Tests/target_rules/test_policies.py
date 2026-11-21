"""Phase 2d: keep/decline decisions come from the platform's policies.

* every ReasonCode has a Snowflake decision, worded exactly as the Snowflake
  path worded it before the move (the strings below are copied from the old
  code: converter/osi_to_sml.py and classify_dax_ast_not_applicable);
* the decision API (decide, reason_text, by-design prefix, placeholder);
* the registry;
* a test-only platform with different policies changes the decisions, the
  classifier's wording and the renderer's placeholder check without any
  edit to shared code.
Snowflake's real output is covered byte for byte by Tests/golden.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace as NS

import pytest

from semabridge.target_rules import (
    SNOWFLAKE_RULES,
    Decision,
    DecisionRule,
    ReasonCode,
    TargetPolicies,
    TargetRules,
    UnhandledReasonError,
    UnsupportedTargetError,
    get_target_rules,
    register_target_rules,
    registered_targets,
)
from semabridge.target_rules import registry as registry_module

POLICIES = SNOWFLAKE_RULES.policies

# The exact strings the Snowflake path wrote before 2d (copied from the old code).
OLD_SNOWFLAKE_TEXT = {
    ReasonCode.DATEADD_NOT_LITERAL: "not applicable: DATEADD shift amount must be a literal integer",
    ReasonCode.TEXT_RESULT: "not applicable: text result",
    ReasonCode.CONSTANT: "not applicable: constant",
    ReasonCode.WINDOW_CASE: "not applicable: CASE WHEN over a window metric is not supported in Snowflake semantic views",
    ReasonCode.TREATAS: "not applicable: virtual relationship (TREATAS) not supported in semantic views",
    ReasonCode.RANKING: "not applicable: needs window metric (ranking)",
    ReasonCode.ALLSELECTED: "not applicable: depends on report selection (ALLSELECTED)",
    ReasonCode.NEEDS_WINDOW: "not applicable: needs window metric",
    ReasonCode.NOT_DECOMPOSABLE_WINDOW: "not applicable: not decomposable for a window",
    ReasonCode.NON_ADDITIVE_TIME_INTEL: "not applicable: depends on non-additive metric",
    ReasonCode.SEMI_ADDITIVE_CASE: "not applicable: CASE WHEN over a semi-additive metric is not supported in Snowflake semantic views [Closing]",
    ReasonCode.NON_ADDITIVE_DEPENDENCY: "not applicable: depends on non-additive metric [Closing]",
    ReasonCode.DISCONNECTED_SELECTOR: "not applicable: selector over a disconnected table",
    ReasonCode.EMPTY_EXPRESSION: "not applicable: no expression",
    ReasonCode.MISSING_COLUMNS: (
        "not applicable: the Power BI measure refers to column(s) missing from the "
        "source model, so it is broken in Power BI too: Sales[Gone]"
    ),
    ReasonCode.CONSTANT_EXPRESSION: (
        "BY_DESIGN_EXCLUDED: expression has no column/measure/table "
        "reference (a compile-time constant) — there is nothing to "
        "aggregate, so this is not a DAX translation failure."
    ),
    ReasonCode.STRING_FUNCTION_ROOT: (
        "BY_DESIGN_EXCLUDED: expression's root operation produces a "
        "string value, not a numeric aggregate — Snowflake "
        "semantic-view metrics must be aggregate expressions. This "
        "field should be modeled as a dimension attribute instead."
    ),
    ReasonCode.CONTEXT_TRANSITION: "BY_DESIGN_EXCLUDED: some analyser reason",
}
DETAIL = {
    ReasonCode.SEMI_ADDITIVE_CASE: "Closing",
    ReasonCode.NON_ADDITIVE_DEPENDENCY: "Closing",
    ReasonCode.MISSING_COLUMNS: "Sales[Gone]",
    ReasonCode.CONTEXT_TRANSITION: "some analyser reason",
}


# --- Snowflake wording ----------------------------------------------------------

def test_every_reason_code_has_a_snowflake_rule() -> None:
    assert set(POLICIES.rules) == set(ReasonCode)
    assert set(OLD_SNOWFLAKE_TEXT) == set(ReasonCode)


@pytest.mark.parametrize("code", list(ReasonCode))
def test_snowflake_wording_is_unchanged(code: ReasonCode) -> None:
    decision = POLICIES.decide(code, DETAIL.get(code))
    assert decision == Decision(keep=False, reason=OLD_SNOWFLAKE_TEXT[code])


def test_window_reason_with_by_design_prefix() -> None:
    # osi_to_sml's stale-reason clean-up wrote this literal before 2d.
    assert (
        POLICIES.reason_text(ReasonCode.NOT_DECOMPOSABLE_WINDOW, by_design=True)
        == "BY_DESIGN_EXCLUDED: not applicable: not decomposable for a window"
    )


def test_snowflake_declines_text_and_constants() -> None:
    assert POLICIES.decline_text_measures is True
    assert POLICIES.decline_constant_measures is True


# --- placeholder ------------------------------------------------------------------

def test_placeholder_and_marker() -> None:
    assert POLICIES.placeholder_sql == "CAST(NULL AS DOUBLE)"
    assert POLICIES.placeholder_marker == "CAST(NULL AS"
    assert POLICIES.is_placeholder("(cast(null as double))")
    assert POLICIES.is_placeholder("CAST(NULL AS NUMBER)")  # any type, as before
    assert not POLICIES.is_placeholder("SUM(SALES.\"AMOUNT\")")
    assert not POLICIES.is_placeholder("")
    assert not POLICIES.is_placeholder(None)


def test_placeholder_marker_without_as() -> None:
    policies = TargetPolicies(placeholder_sql="NULL", by_design_prefix="X", decline_two_table_measures=False)
    assert policies.placeholder_marker == "NULL"


# --- API shape ------------------------------------------------------------------------

def test_unhandled_code_fails_loudly() -> None:
    empty = TargetPolicies(placeholder_sql="NULL", by_design_prefix="X", decline_two_table_measures=False)
    with pytest.raises(UnhandledReasonError):
        empty.decide(ReasonCode.TEXT_RESULT)


def test_policies_are_read_only() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        POLICIES.placeholder_sql = "NULL"  # type: ignore[misc]
    with pytest.raises(TypeError):
        POLICIES.rules[ReasonCode.TEXT_RESULT] = DecisionRule(keep=True)  # type: ignore[index]


# --- registry --------------------------------------------------------------------------

def test_registry() -> None:
    assert "snowflake" in registered_targets()
    assert get_target_rules("SNOWFLAKE") is SNOWFLAKE_RULES
    with pytest.raises(UnsupportedTargetError):
        get_target_rules("databricks")
    other = dataclasses.replace(SNOWFLAKE_RULES, target="registry-test-platform")
    try:
        assert register_target_rules(other) is other
        assert register_target_rules(other) is other  # same object again: no-op
        with pytest.raises(ValueError):
            register_target_rules(dataclasses.replace(other))  # different object, same name
        assert get_target_rules("registry-test-platform") is other
    finally:
        registry_module._REGISTRY.pop("registry-test-platform", None)


# --- a different platform changes decisions without touching shared code ---------------

def _keeps_text_rules() -> TargetRules:
    rules = dict(POLICIES.rules)
    rules[ReasonCode.STRING_FUNCTION_ROOT] = DecisionRule(keep=True)
    rules[ReasonCode.TEXT_RESULT] = DecisionRule(keep=True)
    rules[ReasonCode.ALLSELECTED] = DecisionRule(keep=False, text="test platform: no ALLSELECTED")
    return TargetRules(
        target="keeps-text-test",
        policies=TargetPolicies(
            placeholder_sql="MARKER_NULL",
            by_design_prefix="BY_DESIGN_EXCLUDED",
            decline_two_table_measures=False,
            rules=rules,
        ),
        capabilities=SNOWFLAKE_RULES.capabilities,
        dialect=SNOWFLAKE_RULES.dialect,
    )


def test_converter_decides_through_the_rules() -> None:
    from semabridge.converter.osi_to_sml import OSIToSMLConverter

    measure = NS(
        unique_name="Fmt", dataset="Sales", expression='FORMAT(SUM(Sales[Amount]), "0")',
        sql_expression=None, source_column=None, aggregation=NS(name="SUM"), label="Fmt",
        description="", format_string=None, is_hidden=False, access_modifier=None,
        synonyms=[], synonym_sources={}, has_report_alias=False, source_file=None,
        original_name=None, complexity_tier=1,
    )
    snowflake = OSIToSMLConverter()._convert_metric(measure, known_measure_names={"Fmt"})
    assert snowflake.sync_failure_reason == OLD_SNOWFLAKE_TEXT[ReasonCode.STRING_FUNCTION_ROOT]

    other = OSIToSMLConverter(rules=_keeps_text_rules())._convert_metric(measure, known_measure_names={"Fmt"})
    # Kept by the converter's decision: not declined "by design".
    assert not (other.sync_failure_reason or "").startswith("BY_DESIGN_EXCLUDED")


def test_converter_placeholder_comes_from_the_rules() -> None:
    from semabridge.converter.osi_to_sml import OSIToSMLConverter

    measure = NS(
        unique_name="Empty", dataset="Sales", expression="", sql_expression=None, source_column=None,
        aggregation=NS(name="SUM"), label="Empty", description="", format_string=None, is_hidden=False,
        access_modifier=None, synonyms=[], synonym_sources={}, has_report_alias=False, source_file=None,
        original_name=None, complexity_tier=1,
    )
    assert OSIToSMLConverter()._convert_metric(measure).sql_expression == "CAST(NULL AS DOUBLE)"
    assert OSIToSMLConverter(rules=_keeps_text_rules())._convert_metric(measure).sql_expression == "MARKER_NULL"


def test_classifier_wording_follows_the_rules() -> None:
    from semabridge.converter.dax_ast_parser import (
        DaxAstParser,
        classify_dax_ast_not_applicable,
        classify_dax_ast_reason,
    )

    tree = DaxAstParser().parse("DIVIDE(SUM(Sales[A]), CALCULATE(SUM(Sales[A]), ALLSELECTED(Sales)))")
    assert classify_dax_ast_reason(tree) == (ReasonCode.ALLSELECTED, None)
    assert classify_dax_ast_not_applicable(tree) == OLD_SNOWFLAKE_TEXT[ReasonCode.ALLSELECTED]
    assert classify_dax_ast_not_applicable(tree, rules=_keeps_text_rules()) == "test platform: no ALLSELECTED"


def test_renderer_placeholder_check_follows_the_rules() -> None:
    from semabridge.converter.dax_ast_parser import try_ast_translate

    errors: list = []
    out = try_ast_translate(
        "[Dep] + 1", table_alias="SALES", measure_sql_map={"Dep": "MARKER_NULL"},
        rules=_keeps_text_rules(), render_errors=errors,
    )
    assert out is None and any("untranslatable" in e for e in errors)
    # Snowflake does not treat that text as a placeholder.
    assert try_ast_translate("[Dep] + 1", table_alias="SALES", measure_sql_map={"Dep": "MARKER_NULL"}) is not None
