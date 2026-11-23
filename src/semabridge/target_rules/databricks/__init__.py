"""Databricks rules: Unity Catalog metric views.

    dialect.py       how Databricks SQL is spelled
    policies.py      keep/decline per ReasonCode + wording, placeholder
    capabilities.py  what metric views support (starting values)

Used only when the target is Databricks; Snowflake's rules are untouched.
"""

from __future__ import annotations

from semabridge.target_rules.base import TargetRules
from semabridge.target_rules.databricks.capabilities import DATABRICKS_CAPABILITIES
from semabridge.target_rules.databricks.dialect import DatabricksDialect
from semabridge.target_rules.databricks.policies import (
    BY_DESIGN_PREFIX,
    PLACEHOLDER_SQL,
    DATABRICKS_POLICIES,
)
from semabridge.target_rules.registry import register_target_rules

DATABRICKS_RULES = TargetRules(
    target="databricks",
    policies=DATABRICKS_POLICIES,
    capabilities=DATABRICKS_CAPABILITIES,
    dialect=DatabricksDialect(),
)

register_target_rules(DATABRICKS_RULES)

__all__ = [
    "BY_DESIGN_PREFIX",
    "PLACEHOLDER_SQL",
    "DATABRICKS_CAPABILITIES",
    "DATABRICKS_POLICIES",
    "DATABRICKS_RULES",
    "DatabricksDialect",
]
