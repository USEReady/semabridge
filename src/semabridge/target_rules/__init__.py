"""Per-platform rules: policies, capabilities and SQL dialect.

    from semabridge.target_rules import get_target_rules
    rules = get_target_rules("snowflake")

base.py      the shapes: TargetRules, TargetPolicies (ReasonCode -> Decision),
             TargetCapabilities, SqlDialect
registry.py  platforms register their rules; shared code looks them up
snowflake/   Snowflake's rules (dialect, policies, capabilities)
databricks/  Databricks' rules (Phase 3.1): Unity Catalog metric views

Snowflake's rules reproduce today's behaviour and stay the default; each
platform is a sub-package that registers itself.
"""

from __future__ import annotations

from semabridge.target_rules.base import (
    Decision,
    DecisionRule,
    ReasonCode,
    SqlDialect,
    TargetCapabilities,
    TargetPolicies,
    TargetRules,
    UnhandledReasonError,
)
from semabridge.target_rules.registry import (
    UnsupportedTargetError,
    get_target_rules,
    register_target_rules,
    registered_targets,
)

# Importing a platform's package registers its rules.
from semabridge.target_rules.snowflake import SNOWFLAKE_RULES, SnowflakeDialect  # noqa: E402
from semabridge.target_rules.databricks import DATABRICKS_RULES, DatabricksDialect  # noqa: E402

__all__ = [
    "DATABRICKS_RULES",
    "DatabricksDialect",
    "Decision",
    "DecisionRule",
    "ReasonCode",
    "SNOWFLAKE_RULES",
    "SnowflakeDialect",
    "SqlDialect",
    "TargetCapabilities",
    "TargetPolicies",
    "TargetRules",
    "UnhandledReasonError",
    "UnsupportedTargetError",
    "get_target_rules",
    "register_target_rules",
    "registered_targets",
]
