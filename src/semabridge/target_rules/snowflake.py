"""Snowflake rules: today's Snowflake behaviour, unchanged.

Every value here must equal what the Snowflake path produces today. Where a
helper or constant already exists it is called or imported, not copied, so
the two cannot drift apart. Tests/target_rules checks each value against the
live code and the golden output.
"""

from __future__ import annotations

from semabridge.target_rules.base import (
    SqlDialect,
    TargetCapabilities,
    TargetPolicies,
    TargetRules,
)
from semabridge.utils.naming import sanitize_column, to_alias

# Same values as the constants in converter/dax_ast_parser.py
# (BY_DESIGN_EXCLUDED_PREFIX) and connectors/metrics_clause_builder.py
# (NULL_PLACEHOLDER_SQL). Kept as literals here so importing the rules does
# not import the 8,700-line DAX parser; the tests assert they are equal.
_BY_DESIGN_PREFIX = "BY_DESIGN_EXCLUDED"
_PLACEHOLDER_SQL = "CAST(NULL AS DOUBLE)"

# Reason wording exactly as converter/osi_to_sml.py writes it today.
_CONSTANT_MEASURE_REASON = (
    "expression has no column/measure/table reference (a compile-time constant) "
    "— there is nothing to aggregate, so this is not a DAX translation failure."
)
_TEXT_MEASURE_REASON = (
    "expression's root operation produces a string value, not a numeric aggregate "
    "— Snowflake semantic-view metrics must be aggregate expressions. This "
    "field should be modeled as a dimension attribute instead."
)


class SnowflakeDialect(SqlDialect):
    """Snowflake spelling as the DAX renderer writes it today.

    Each method returns exactly the text the renderer wrote before the
    spelling moved here (converter/dax_ast_parser.py, DaxSqlRenderer).
    """

    name = "snowflake"
    identifier_quote = '"'
    float_cast_suffix = "::FLOAT"
    iff_function = "IFF"

    def table_alias(self, table_name: str) -> str:
        return to_alias(table_name)

    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def column_ref(self, alias: str, column_name: str) -> str:
        # DaxSqlRenderer._render_column_ref: f'{alias}."{sanitize_column(col)}"'
        return self.qualify(alias, sanitize_column(column_name))

    def try_to_number(self, expr: str) -> str:
        return f"TRY_TO_NUMBER({expr})"

    def to_varchar(self, expr: str) -> str:
        return f"TO_VARCHAR({expr})"

    def current_date(self) -> str:
        return "CURRENT_DATE()"

    def current_timestamp(self) -> str:
        return "CURRENT_TIMESTAMP()"

    def date_add(self, unit: str, amount: "str | int", expr: str) -> str:
        return f"DATEADD({unit}, {amount}, {expr})"

    def date_trunc(self, unit: str, expr: str) -> str:
        return f"DATE_TRUNC('{unit}', {expr})"

    def last_day(self, expr: str, unit: str) -> str:
        return f"LAST_DAY({expr}, '{unit}')"

    def date_diff(self, unit: str, start: str, end: str) -> str:
        return f"DATEDIFF({unit}, {start}, {end})"


SNOWFLAKE_RULES = TargetRules(
    target="snowflake",
    policies=TargetPolicies(
        decline_text_measures=True,
        decline_constant_measures=True,
        decline_two_table_measures=True,
        placeholder_sql=_PLACEHOLDER_SQL,
        by_design_prefix=_BY_DESIGN_PREFIX,
        constant_measure_reason=_CONSTANT_MEASURE_REASON,
        text_measure_reason=_TEXT_MEASURE_REASON,
    ),
    capabilities=TargetCapabilities(
        text_measures=False,
        window_metrics=True,
        nested_joins=True,
        bridge_views=False,
    ),
    dialect=SnowflakeDialect(),
)
