"""Databricks naming: Snowflake's rule, decided 2026-11-23.

Names are made exactly as for Snowflake (upper case; %, $ ... spelled out
as PCT, DOL ...; COL_ only for words Snowflake truly reserves), so the same
model gets the same names on both platforms and "Margin" / "Margin %" never
collide. Databricks identifiers are case-insensitive, so upper case is safe;
the original Power BI name is kept as display name / synonym.

Differences from Snowflake:
  * no reserved-keyword block: every identifier is backtick-quoted on
    Databricks, so reserved words are legal names;
  * no legacy-name clean-up: Databricks projects never had the old
    automatic COL_ names.
To give Databricks its own rule later, change only this file.
"""

from __future__ import annotations

from typing import Optional

from semabridge.target_rules.snowflake.naming import SnowflakeNaming


class DatabricksNaming(SnowflakeNaming):
    platform_label = "Databricks"
    normalizes_user_names = True
    ignores_legacy_auto_names = False

    def reserved_words(self, entity_kind: Optional[str] = None) -> frozenset:
        return frozenset()

    def is_legacy_auto_name(self, source_path: Optional[str], name: str) -> bool:
        return False
