"""Warehouse Platform Adapters for Drift Monitoring."""

from __future__ import annotations

from semabridge.governance.drift.adapters.base import PlatformAdapter
from semabridge.governance.drift.adapters.databricks import DatabricksAdapter
from semabridge.governance.drift.adapters.fixture import RecordedFixtureAdapter
from semabridge.governance.drift.adapters.snowflake import SnowflakeAdapter
from semabridge.governance.drift.adapters.registry import (
    get_adapter,
    is_mock_allowed,
    is_platform_supported,
    list_supported_platforms,
    resolve_project_platform,
)


class DefinitionUnreadable(ValueError):
    """Raised when definition cannot be parsed or is unreadable."""
    pass


def parse_ddl(text: str, view_name: str, platform: str = "snowflake", **kwargs):
    if not text or not str(text).strip():
        raise DefinitionUnreadable("empty definition")
    adapter = get_adapter(platform)
    try:
        return adapter.parse_definition(text, view_name, source="ddl", **kwargs)
    except Exception as e:
        raise DefinitionUnreadable(str(e)) from e


def parse_yaml(text: str, view_name: str, platform: str = "databricks", capabilities=None, **kwargs):
    if not text or not str(text).strip():
        raise DefinitionUnreadable("empty definition")
    adapter = get_adapter(platform)
    try:
        return adapter.parse_definition(text, view_name, source="yaml", **kwargs)
    except Exception as e:
        raise DefinitionUnreadable(str(e)) from e


def assert_names_are_identifiers(definition):
    import re
    ident_re = re.compile(r'^[A-Za-z0-9_]+$')
    for obj in definition.objects:
        clean = (obj.name or "").strip().strip('"`')
        if not ident_re.match(clean):
            raise AssertionError(f"Object name '{obj.name}' is not a valid plain identifier")


def split_top_level(text: str, separator: str = ","):
    parts, depth, quote, current = [], 0, None, []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            current.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == separator and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


__all__ = [
    "PlatformAdapter",
    "DatabricksAdapter",
    "RecordedFixtureAdapter",
    "SnowflakeAdapter",
    "get_adapter",
    "is_mock_allowed",
    "is_platform_supported",
    "list_supported_platforms",
    "resolve_project_platform",
    "parse_ddl",
    "parse_yaml",
    "assert_names_are_identifiers",
    "split_top_level",
    "DefinitionUnreadable",
]
