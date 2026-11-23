"""How Databricks SQL is spelled for the shared DAX renderer.

Every choice follows the Databricks SQL reference (checked 2026-11-23):

* identifiers are quoted with backticks; a backtick inside is doubled
  (sql-ref-identifiers);
* ``expr::DOUBLE`` is the ``::`` cast operator (functions/cast);
* ``IF(cond, a, b)`` (``IFF`` is a synonym; ``IF`` is the documented name);
* ``try_to_number`` needs a format argument, so text -> number uses
  ``TRY_CAST(... AS DOUBLE)``, which is NULL on bad input in any ANSI mode;
* ``DATEADD`` / ``DATE_TRUNC`` return TIMESTAMP, so date arithmetic uses the
  DATE-returning ``ADD_MONTHS`` / ``DATE_ADD`` / ``TRUNC``;
* ``LAST_DAY`` only knows months; quarter and year ends are built from
  ``TRUNC`` + ``ADD_MONTHS`` - 1 day;
* the 3-argument ``DATEDIFF`` counts whole elapsed units, while DAX counts
  interval *boundaries* crossed (DAX docs: DATE(2019,7,1) -> DATE(2021,12,31)
  is Year 2, Quarter 9, Month 29, Week 130, Day 914), so date differences
  are computed from calendar parts.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from semabridge.target_rules.base import SqlDialect
from semabridge.utils.naming import sanitize_column, to_alias

# Units the DATE-returning helpers handle, as months per unit.
_MONTHS_PER_UNIT = {"MONTH": 1, "QUARTER": 3, "YEAR": 12}
# Units TRUNC(date, unit) supports (returns DATE).
_TRUNC_UNITS = {"YEAR", "QUARTER", "MONTH", "WEEK"}


def _unit(unit: str) -> str:
    return str(unit or "").strip().strip("'\"").upper()


def _times(amount: "str | int", factor: int) -> str:
    """amount * factor, folded when amount is a plain integer."""
    if factor == 1:
        return str(amount)
    text = str(amount).strip()
    try:
        return str(int(text) * factor)
    except ValueError:
        return f"({text}) * {factor}"


class DatabricksDialect(SqlDialect):
    """Databricks SQL spelling (Unity Catalog, Databricks SQL warehouses).

    ``column_names`` optionally maps (table alias, model column name), both
    upper-cased, to the real column name in the matched Unity Catalog table.
    Table matching (Phase 3.4) fills it in; without an entry the column name
    is cleaned the same way as for Snowflake.
    """

    name = "databricks"
    identifier_quote = "`"
    float_cast_suffix = "::DOUBLE"
    iff_function = "IF"

    def __init__(self, column_names: Optional[Mapping[Tuple[str, str], str]] = None) -> None:
        self._column_names = {
            (str(alias).upper(), str(column).upper()): physical
            for (alias, column), physical in (column_names or {}).items()
        }

    # --- identifiers ---------------------------------------------------------

    def table_alias(self, table_name: str) -> str:
        return to_alias(table_name)

    def quote_identifier(self, name: str) -> str:
        return "`" + str(name).replace("`", "``") + "`"

    def column_ref(self, alias: str, column_name: str) -> str:
        physical = self._column_names.get((str(alias).upper(), str(column_name).upper()))
        return self.qualify(alias, physical if physical else sanitize_column(column_name))

    def measure_ref(self, measure_name: str) -> str:
        """Reference to another measure of the same metric view (Databricks only)."""
        return f"MEASURE({self.quote_identifier(measure_name)})"

    # --- casts and conditionals ----------------------------------------------

    def try_to_number(self, expr: str) -> str:
        return f"TRY_CAST({expr} AS DOUBLE)"

    def to_varchar(self, expr: str) -> str:
        return f"CAST({expr} AS STRING)"

    # --- dates ---------------------------------------------------------------

    def current_date(self) -> str:
        return "CURRENT_DATE()"

    def current_timestamp(self) -> str:
        return "CURRENT_TIMESTAMP()"

    def date_add(self, unit: str, amount: "str | int", expr: str) -> str:
        u = _unit(unit)
        if u in _MONTHS_PER_UNIT:
            return f"ADD_MONTHS({expr}, {_times(amount, _MONTHS_PER_UNIT[u])})"
        if u == "DAY":
            return f"DATE_ADD({expr}, {amount})"
        if u == "WEEK":
            return f"DATE_ADD({expr}, {_times(amount, 7)})"
        # Time units keep the time of day: TIMESTAMP arithmetic.
        return f"TIMESTAMPADD({u}, {amount}, {expr})"

    def date_trunc(self, unit: str, expr: str) -> str:
        u = _unit(unit)
        if u in _TRUNC_UNITS:
            return f"TRUNC({expr}, '{u}')"
        if u == "DAY":
            return f"CAST({expr} AS DATE)"
        return f"DATE_TRUNC('{u}', {expr})"

    def last_day(self, expr: str, unit: str) -> str:
        u = _unit(unit)
        if u == "MONTH":
            return f"LAST_DAY({expr})"
        if u in ("QUARTER", "YEAR"):
            months = _MONTHS_PER_UNIT[u]
            return f"DATE_ADD(ADD_MONTHS(TRUNC({expr}, '{u}'), {months}), -1)"
        # The renderer only asks for YEAR / QUARTER / MONTH; anything else
        # gets the month end (never raise: the renderer only catches its own
        # decline errors).
        return f"LAST_DAY({expr})"

    def date_diff(self, unit: str, start: str, end: str) -> str:
        """Number of `unit` boundaries crossed from start to end (DAX DATEDIFF)."""
        u = _unit(unit)
        if u == "YEAR":
            return f"(YEAR({end}) - YEAR({start}))"
        if u == "QUARTER":
            return f"((YEAR({end}) - YEAR({start})) * 4 + QUARTER({end}) - QUARTER({start}))"
        if u == "MONTH":
            return f"((YEAR({end}) - YEAR({start})) * 12 + MONTH({end}) - MONTH({start}))"
        if u == "DAY":
            return f"DATEDIFF(CAST({end} AS DATE), CAST({start} AS DATE))"
        if u == "WEEK":
            # Weeks starting on Sunday: whole weeks between the two weeks' Sundays.
            return (
                f"(DATEDIFF(DATE_SUB(CAST({end} AS DATE), DAYOFWEEK({end}) - 1), "
                f"DATE_SUB(CAST({start} AS DATE), DAYOFWEEK({start}) - 1)) DIV 7)"
            )
        if u in ("HOUR", "MINUTE", "SECOND"):
            # Whole units between the two truncated instants = boundaries crossed.
            return f"TIMESTAMPDIFF({u}, DATE_TRUNC('{u}', {start}), DATE_TRUNC('{u}', {end}))"
        # DAX allows only the units above; pass anything else through (as the
        # Snowflake dialect does) so the SQL fails visibly rather than the
        # renderer crashing.
        return f"TIMESTAMPDIFF({u}, {start}, {end})"
