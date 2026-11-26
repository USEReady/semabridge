"""How Snowflake SQL is spelled: exactly what the DAX renderer wrote before
the spelling moved here (converter/dax_ast_parser.py, DaxSqlRenderer)."""

from __future__ import annotations

from semabridge.target_rules.base import SqlDialect
from semabridge.utils.naming import sanitize_column, to_alias


class SnowflakeDialect(SqlDialect):
    """Snowflake spelling as the DAX renderer writes it today."""

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
