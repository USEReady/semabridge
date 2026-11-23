"""Databricks' keep/decline decisions and their wording.

Same decisions as Snowflake today (every code is a decline): a Unity Catalog
metric view measure must be an aggregate expression, so the same DAX
constructs are not expressible yet. Window measures and two-table measures
are revisited when capability probes (3.5) and the planner's bridge views
(3.6) exist. Wording names Databricks metric views instead of Snowflake
semantic views, and keeps the "not applicable" / by-design prefixes the
translator relies on.

Declined measures get a placeholder like Snowflake's, but in aggregate form:
a metric view measure must contain an aggregate function, so Snowflake's
CAST(NULL AS DOUBLE) is not a valid measure there. MAX(CAST(NULL AS DOUBLE))
is the same NULL as an aggregate; it is confirmed by a live test before the
YAML writer (3.7) relies on it, else declined measures are left out.
"""

from __future__ import annotations

from semabridge.target_rules.base import DecisionRule, ReasonCode, TargetPolicies

BY_DESIGN_PREFIX = "BY_DESIGN_EXCLUDED"
PLACEHOLDER_SQL = "MAX(CAST(NULL AS DOUBLE))"

_DATABRICKS_DECISIONS = {
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
            "— Databricks metric view measures must be aggregate expressions. This "
            "field should be modeled as a dimension (field) instead."
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
        text="not applicable: CASE WHEN over a window metric is not supported in Databricks metric views yet",
    ),
    ReasonCode.TREATAS: DecisionRule(
        keep=False, text="not applicable: virtual relationship (TREATAS) not supported in metric views"
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
            "Databricks metric views yet [{detail}]"
        ),
    ),
    ReasonCode.NON_ADDITIVE_DEPENDENCY: DecisionRule(
        keep=False, text="not applicable: depends on non-additive metric [{detail}]"
    ),
    ReasonCode.DISCONNECTED_SELECTOR: DecisionRule(
        keep=False, text="not applicable: selector over a disconnected table"
    ),
}

DATABRICKS_POLICIES = TargetPolicies(
    placeholder_sql=PLACEHOLDER_SQL,
    by_design_prefix=BY_DESIGN_PREFIX,
    # Kept by bridge views once the planner exists (3.6); declined until then.
    decline_two_table_measures=True,
    rules=_DATABRICKS_DECISIONS,
    # The relationship advisories predict a Snowflake semantic-view compile
    # error (010211); they do not apply here.
    relationship_advisories=False,
)
