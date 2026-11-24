"""Snowflake naming: today's rule, unchanged.

Every method calls the existing functions in utils/identifiers.py
(snowflake_target_name, is_legacy_auto_column_name and the reserved-word
lists); nothing is re-implemented here. Tests/target_rules/test_naming_parity.py
checks the output against names recorded before this module existed.
"""

from __future__ import annotations

from typing import Optional

from semabridge.target_rules.base import TargetNaming


class SnowflakeNaming(TargetNaming):
    platform_label = "Snowflake"
    normalizes_user_names = True
    ignores_legacy_auto_names = True

    def target_name(self, name: str, source_path: Optional[str] = None, entity_kind: Optional[str] = None) -> str:
        from semabridge.utils.identifiers import snowflake_target_name

        return snowflake_target_name(name, source_path, entity_kind)

    def reserved_words(self, entity_kind: Optional[str] = None) -> frozenset:
        from semabridge.utils.identifiers import SNOWFLAKE_ALIAS_RESERVED_WORDS, SNOWFLAKE_RESERVED_WORDS

        kind = str(entity_kind or "column").strip().lower() or "column"
        return frozenset(SNOWFLAKE_ALIAS_RESERVED_WORDS if kind == "column" else SNOWFLAKE_RESERVED_WORDS)

    def is_legacy_auto_name(self, source_path: Optional[str], name: str) -> bool:
        from semabridge.utils.identifiers import is_column_source_path, is_legacy_auto_column_name

        path = str(source_path or "")
        return is_column_source_path(path) and is_legacy_auto_column_name(path.split(".columns.", 1)[1], name)
