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
*spelling* its SQL is not. Shared code reports WHAT it found as a neutral
``ReasonCode``; the platform's policies decide keep/decline and word the
reason. Shared code asks the rules instead of hard-coding Snowflake choices.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Optional

# Deliberately imports nothing from semabridge.converter or
# semabridge.dax_translation: the DAX renderer imports these rules, so
# importing back would be circular.


class ReasonCode(str, Enum):
    """Why a measure may not be expressible, found by shared code.

    Platform-neutral: the code says what the DAX is or does; each platform's
    policies decide whether that means keep or decline, and how to word it.
    """

    # Found by the converter (osi_to_sml) from the measure's facts.
    MISSING_COLUMNS = "missing_columns"            # detail: the missing refs
    EMPTY_EXPRESSION = "empty_expression"
    CONSTANT_EXPRESSION = "constant_expression"    # no column/measure/table ref
    STRING_FUNCTION_ROOT = "string_function_root"  # string function/literal at the root
    CONTEXT_TRANSITION = "context_transition"      # detail: the analyser's reason

    # Found by the DAX classifier before rendering
    # (dax_ast_parser.classify_dax_ast_reason).
    DATEADD_NOT_LITERAL = "dateadd_not_literal"
    TEXT_RESULT = "text_result"
    CONSTANT = "constant"
    WINDOW_CASE = "window_case"                    # IF/CASE over a window construct
    TREATAS = "treatas"                            # virtual relationship
    RANKING = "ranking"
    ALLSELECTED = "allselected"                    # depends on report selection
    NEEDS_WINDOW = "needs_window"
    NOT_DECOMPOSABLE_WINDOW = "not_decomposable_window"
    NON_ADDITIVE_TIME_INTEL = "non_additive_time_intel"
    SEMI_ADDITIVE_CASE = "semi_additive_case"      # detail: the measure
    NON_ADDITIVE_DEPENDENCY = "non_additive_dependency"  # detail: the measure
    DISCONNECTED_SELECTOR = "disconnected_selector"


@dataclass(frozen=True)
class DecisionRule:
    """How one platform treats one ReasonCode."""

    keep: bool
    # Reason text when declined; may contain "{detail}".
    text: str = ""
    # Prefix the reason with the policy's by-design marker.
    by_design: bool = False


@dataclass(frozen=True)
class Decision:
    """A platform's verdict on one measure."""

    keep: bool
    # The reason stored on a declined measure (sync_failure_reason).
    reason: Optional[str] = None


class UnhandledReasonError(KeyError):
    """A platform's policies have no rule for a ReasonCode."""


@dataclass(frozen=True)
class TargetPolicies:
    """Fixed decisions about measures for one target platform."""

    # SQL a declined or empty measure is given as a stand-in.
    placeholder_sql: str
    # Prefix marking a decline as "by design" rather than a translation gap.
    by_design_prefix: str
    # Measures that need two fact tables (e.g. [Sales] / [Budget]).
    decline_two_table_measures: bool
    # How each ReasonCode is decided and worded on this platform.
    rules: Mapping[ReasonCode, DecisionRule] = field(default_factory=dict)
    # Text that marks SQL as a placeholder (a declined measure's stand-in),
    # matched case-insensitively. Defaults to the start of placeholder_sql
    # up to "AS".
    placeholder_marker: str = ""
    # Add the relationship-graph advisories after translation (measures
    # whose CALCULATE filters a table their own table cannot / can only
    # through enrichment reach). They predict Snowflake semantic-view
    # compile errors (010211), so only Snowflake turns them on.
    relationship_advisories: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "rules", MappingProxyType(dict(self.rules)))
        if not self.placeholder_marker:
            marker = self.placeholder_sql
            cut = marker.upper().find(" AS ")
            marker = marker[: cut + len(" AS")] if cut >= 0 else marker
            object.__setattr__(self, "placeholder_marker", marker)

    def rule_for(self, code: ReasonCode) -> DecisionRule:
        try:
            return self.rules[ReasonCode(code)]
        except KeyError:
            raise UnhandledReasonError(f"no policy for reason code {code!r}") from None

    def reason_text(self, code: ReasonCode, detail: Optional[str] = None, by_design: Optional[bool] = None) -> str:
        """The worded reason for a code (with the by-design prefix when the
        rule, or the caller via by_design=True/False, says so)."""
        rule = self.rule_for(code)
        text = rule.text.format(detail=detail if detail is not None else "")
        prefixed = rule.by_design if by_design is None else by_design
        return f"{self.by_design_prefix}: {text}" if prefixed else text

    def decide(self, code: ReasonCode, detail: Optional[str] = None, by_design: Optional[bool] = None) -> Decision:
        """Keep or decline a measure for a ReasonCode, with its worded reason."""
        rule = self.rule_for(code)
        if rule.keep:
            return Decision(keep=True)
        return Decision(keep=False, reason=self.reason_text(code, detail, by_design))

    def by_design_reason(self, reason: str) -> str:
        """Full decline reason as stored on a measure: '<prefix>: <reason>'."""
        return f"{self.by_design_prefix}: {reason}"

    def is_placeholder(self, sql: Optional[str]) -> bool:
        """True when SQL is (or contains) a declined measure's placeholder."""
        return bool(sql) and self.placeholder_marker.upper() in str(sql).upper()

    @property
    def decline_text_measures(self) -> bool:
        return not self.rule_for(ReasonCode.TEXT_RESULT).keep

    @property
    def decline_constant_measures(self) -> bool:
        return not self.rule_for(ReasonCode.CONSTANT).keep


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
    # Measures may read helper columns precomputed on an enriched source view
    # (Snowflake: whole-table totals such as "_ALL_COUNTA_..."). Without it
    # the renderer declines measures that need them.
    enriched_helper_columns: bool = False


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
