"""Snowflake's keep/decline decisions and their exact wording.

Every text here is the string the Snowflake path wrote before it moved
here: the converter's declines (converter/osi_to_sml.py) and the DAX
classifier's "not applicable" reasons (converter/dax_ast_parser.py,
classify_dax_ast_not_applicable). Snowflake semantic views only hold
aggregate metrics, so every code below is a decline. Tests/target_rules and
the golden tests check each string against today's output.
"""

from __future__ import annotations

from semabridge.target_rules.base import DecisionRule, ReasonCode, TargetPolicies

# Same values as converter/dax_ast_parser.py (BY_DESIGN_EXCLUDED_PREFIX) and
# connectors/metrics_clause_builder.py (NULL_PLACEHOLDER_SQL). Literals here
# so importing the rules does not import the DAX parser; tests assert equality.
BY_DESIGN_PREFIX = "BY_DESIGN_EXCLUDED"
PLACEHOLDER_SQL = "CAST(NULL AS DOUBLE)"

_SNOWFLAKE_DECISIONS = {
    # --- the converter's checks (osi_to_sml.py) -----------------------------
    ReasonCode.MISSING_COLUMNS: DecisionRule(
        keep=False,
        text=(
            "not applicable: the Power BI measure refers to column(s) missing from the "
            "source model, so it is broken in Power BI too: {detail}"
        ),
    ),
    ReasonCode.EMPTY_EXPRESSION: DecisionRule(keep=False, text="not applicable: no expression"),
    ReasonCode.CONSTANT_EXPRESSION: DecisionRule(
        keep=False,
        by_design=True,
        text=(
            "expression has no column/measure/table reference (a compile-time constant) "
            "— there is nothing to aggregate, so this is not a DAX translation failure."
        ),
    ),
    ReasonCode.STRING_FUNCTION_ROOT: DecisionRule(
        keep=False,
        by_design=True,
        text=(
            "expression's root operation produces a string value, not a numeric aggregate "
            "— Snowflake semantic-view metrics must be aggregate expressions. This "
            "field should be modeled as a dimension attribute instead."
        ),
    ),
    ReasonCode.CONTEXT_TRANSITION: DecisionRule(keep=False, by_design=True, text="{detail}"),
    # --- the DAX classifier (dax_ast_parser.classify_dax_ast_reason) --------
    ReasonCode.DATEADD_NOT_LITERAL: DecisionRule(
        keep=False, text="not applicable: DATEADD shift amount must be a literal integer"
    ),
    ReasonCode.TEXT_RESULT: DecisionRule(keep=False, text="not applicable: text result"),
    ReasonCode.CONSTANT: DecisionRule(keep=False, text="not applicable: constant"),
    ReasonCode.WINDOW_CASE: DecisionRule(
        keep=False,
        text="not applicable: CASE WHEN over a window metric is not supported in Snowflake semantic views",
    ),
    ReasonCode.TREATAS: DecisionRule(
        keep=False, text="not applicable: virtual relationship (TREATAS) not supported in semantic views"
    ),
    ReasonCode.RANKING: DecisionRule(keep=False, text="not applicable: needs window metric (ranking)"),
    ReasonCode.ALLSELECTED: DecisionRule(
        keep=False, text="not applicable: depends on report selection (ALLSELECTED)"
    ),
    ReasonCode.NEEDS_WINDOW: DecisionRule(keep=False, text="not applicable: needs window metric"),
    ReasonCode.NOT_DECOMPOSABLE_WINDOW: DecisionRule(
        keep=False, text="not applicable: not decomposable for a window"
    ),
    ReasonCode.NON_ADDITIVE_TIME_INTEL: DecisionRule(
        keep=False, text="not applicable: depends on non-additive metric"
    ),
    ReasonCode.SEMI_ADDITIVE_CASE: DecisionRule(
        keep=False,
        text=(
            "not applicable: CASE WHEN over a semi-additive metric is not supported in "
            "Snowflake semantic views [{detail}]"
        ),
    ),
    ReasonCode.NON_ADDITIVE_DEPENDENCY: DecisionRule(
        keep=False, text="not applicable: depends on non-additive metric [{detail}]"
    ),
    ReasonCode.DISCONNECTED_SELECTOR: DecisionRule(
        keep=False, text="not applicable: selector over a disconnected table"
    ),
}

SNOWFLAKE_POLICIES = TargetPolicies(
    placeholder_sql=PLACEHOLDER_SQL,
    by_design_prefix=BY_DESIGN_PREFIX,
    decline_two_table_measures=True,
    rules=_SNOWFLAKE_DECISIONS,
)
