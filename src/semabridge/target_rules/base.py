"""The per-platform rules object (Phase 2a of the Databricks plan).

A ``TargetRules`` bundles everything that differs between target platforms,
in three parts:

* ``policies``     -- fixed choices: what is declined, the placeholder SQL a
                      declined measure gets, and how the reason is worded;
* ``capabilities`` -- what the target supports (for Snowflake these are
                      known values; for Databricks they will come from the
                      connect-and-check step);
* ``dialect``      -- how SQL is spelled (quoting, casts, function names).

Understanding a DAX measure is platform-neutral; *deciding* about it and
*spelling* its SQL is not. Shared code asks the rules instead of hard-coding
Snowflake choices.

Status: nothing calls this yet. Later steps move today's hard-coded
Snowflake choices behind it, one at a time, with the golden tests
(Tests/golden) proving Snowflake's output does not change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# Deliberately imports nothing from semabridge.converter or
# semabridge.dax_translation: the DAX renderer will import these rules
# (Phase 2b), so importing back would be circular.


@dataclass(frozen=True)
class TargetPolicies:
    """Fixed decisions about measures for one target platform."""

    # Measures whose result is text (e.g. "Sales: " & FORMAT(...)).
    decline_text_measures: bool
    # Measures with no column/measure/table reference (a constant).
    decline_constant_measures: bool
    # Measures that need two fact tables (e.g. [Sales] / [Budget]).
    decline_two_table_measures: bool
    # SQL a declined or empty measure is given as a stand-in.
    placeholder_sql: str
    # Prefix marking a decline as "by design" rather than a translation gap.
    by_design_prefix: str
    # Reason text (after the prefix) for each by-design decline.
    constant_measure_reason: str
    text_measure_reason: str

    def by_design_reason(self, reason: str) -> str:
        """Full decline reason as stored on a measure: '<prefix>: <reason>'."""
        return f"{self.by_design_prefix}: {reason}"


@dataclass(frozen=True)
class TargetCapabilities:
    """What the target platform can express."""

    # A measure may return text.
    text_measures: bool
    # Window-style metrics (Snowflake: OVER (PARTITION BY EXCLUDING ...)).
    window_metrics: bool
    # Join chains (A -> B -> C) inside one view.
    nested_joins: bool
    # Helper/bridge views may be created to keep two-table measures.
    bridge_views: bool


class SqlDialect(ABC):
    """How SQL is spelled on one target platform.

    Only spellings already tied to existing code live here; more are added
    as each family is moved out of the DAX renderer (Phase 2b).
    """

    # Same value as semabridge.dax_translation.types.Dialect ("snowflake",
    # "databricks"), which Tier-5 uses; kept as a plain string (see imports).
    name: str

    # --- identifiers ---------------------------------------------------------

    # Character that quotes an identifier ('"' Snowflake, '`' Databricks).
    # Also used to recognise quoted identifiers in SQL the renderer reads back.
    identifier_quote: str

    @abstractmethod
    def table_alias(self, table_name: str) -> str:
        """Alias used for a model table inside generated SQL."""

    @abstractmethod
    def quote_identifier(self, name: str) -> str:
        """Quote an already-physical identifier as-is (no renaming)."""

    def qualify(self, alias: str, name: str) -> str:
        """alias.<quoted name>, for a name that is already physical."""
        return f"{alias}.{self.quote_identifier(name)}"

    @abstractmethod
    def column_ref(self, alias: str, column_name: str) -> str:
        """Reference to a model column (logical name) of an aliased table."""

    # --- casts and conditionals ----------------------------------------------

    # Postfix cast to a floating-point number, e.g. "::FLOAT". Also used to
    # strip that cast from SQL the renderer reads back.
    float_cast_suffix: str

    def cast_float(self, expr: str) -> str:
        """Cast an expression to a floating-point number."""
        return f"{expr}{self.float_cast_suffix}"

    # Name of the inline-conditional function, e.g. "IFF".
    iff_function: str

    def iff(self, condition: str, when_true: str, when_false: str) -> str:
        """Inline conditional."""
        return f"{self.iff_function}({condition}, {when_true}, {when_false})"

    @abstractmethod
    def try_to_number(self, expr: str) -> str:
        """Convert text to a number, NULL when it is not one (DAX VALUE)."""

    @abstractmethod
    def to_varchar(self, expr: str) -> str:
        """Convert a value to text."""

    # --- dates ---------------------------------------------------------------

    @abstractmethod
    def current_date(self) -> str:
        """Today's date (DAX TODAY)."""

    @abstractmethod
    def current_timestamp(self) -> str:
        """The current date and time (DAX NOW)."""

    @abstractmethod
    def date_add(self, unit: str, amount: "str | int", expr: str) -> str:
        """expr shifted by amount units (unit as given, e.g. MONTH or month)."""

    @abstractmethod
    def date_trunc(self, unit: str, expr: str) -> str:
        """Start of the unit (YEAR, QUARTER, MONTH, ...) containing expr."""

    @abstractmethod
    def last_day(self, expr: str, unit: str) -> str:
        """Last day of the unit (YEAR, QUARTER, MONTH) containing expr."""

    @abstractmethod
    def date_diff(self, unit: str, start: str, end: str) -> str:
        """Number of unit boundaries between start and end."""


@dataclass(frozen=True)
class TargetRules:
    """Policies, capabilities and dialect for one target platform."""

    target: str
    policies: TargetPolicies
    capabilities: TargetCapabilities
    dialect: SqlDialect
