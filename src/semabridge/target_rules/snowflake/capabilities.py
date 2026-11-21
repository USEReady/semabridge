"""What Snowflake semantic views can express (known values)."""

from __future__ import annotations

from semabridge.target_rules.base import TargetCapabilities

SNOWFLAKE_CAPABILITIES = TargetCapabilities(
    text_measures=False,
    window_metrics=True,
    nested_joins=True,
    bridge_views=False,
)
