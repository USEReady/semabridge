"""Per-platform rules: policies, capabilities and SQL dialect.

    from semabridge.target_rules import get_target_rules
    rules = get_target_rules("snowflake")

See base.py for what each part means. Only Snowflake exists so far
(reproducing today's behaviour); Databricks is added in Phase 3.
"""

from __future__ import annotations

from semabridge.target_rules.base import (
    SqlDialect,
    TargetCapabilities,
    TargetPolicies,
    TargetRules,
)
from semabridge.target_rules.snowflake import SNOWFLAKE_RULES, SnowflakeDialect

_RULES_BY_TARGET = {
    "snowflake": SNOWFLAKE_RULES,
}


class UnsupportedTargetError(ValueError):
    """No rules exist for the requested target platform yet."""


def get_target_rules(target: str) -> TargetRules:
    """Rules for a target platform name such as "snowflake" (case-insensitive)."""
    key = str(target or "").strip().lower()
    try:
        return _RULES_BY_TARGET[key]
    except KeyError:
        supported = ", ".join(sorted(_RULES_BY_TARGET))
        raise UnsupportedTargetError(
            f"No target rules for {target!r} yet (supported: {supported})."
        ) from None


__all__ = [
    "SNOWFLAKE_RULES",
    "SnowflakeDialect",
    "SqlDialect",
    "TargetCapabilities",
    "TargetPolicies",
    "TargetRules",
    "UnsupportedTargetError",
    "get_target_rules",
]
