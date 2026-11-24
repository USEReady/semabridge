"""Snowflake rules: today's Snowflake behaviour, unchanged.

    dialect.py       how Snowflake SQL is spelled
    policies.py      keep/decline per ReasonCode + exact wording, placeholder
    capabilities.py  what Snowflake semantic views support

Every value equals what the Snowflake path produces today; Tests/target_rules
and the golden tests check them against the live code and real output.
"""

from __future__ import annotations

from semabridge.target_rules.base import TargetRules
from semabridge.target_rules.registry import register_target_rules
from semabridge.target_rules.snowflake.capabilities import SNOWFLAKE_CAPABILITIES
from semabridge.target_rules.snowflake.dialect import SnowflakeDialect
from semabridge.target_rules.snowflake.naming import SnowflakeNaming
from semabridge.target_rules.snowflake.policies import (
    BY_DESIGN_PREFIX,
    PLACEHOLDER_SQL,
    SNOWFLAKE_POLICIES,
)

SNOWFLAKE_RULES = TargetRules(
    target="snowflake",
    policies=SNOWFLAKE_POLICIES,
    capabilities=SNOWFLAKE_CAPABILITIES,
    dialect=SnowflakeDialect(),
    naming=SnowflakeNaming(),
)

register_target_rules(SNOWFLAKE_RULES)

__all__ = [
    "BY_DESIGN_PREFIX",
    "PLACEHOLDER_SQL",
    "SNOWFLAKE_CAPABILITIES",
    "SNOWFLAKE_POLICIES",
    "SNOWFLAKE_RULES",
    "SnowflakeDialect",
]
