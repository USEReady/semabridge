#!/usr/bin/env python
"""Refactor legacy Snowflake connect calls in snowflake_emitter.py."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


HELPER_IMPORT = (
    "from semabridge.connectors.snowflake_connection import "
    "get_snowflake_connect_kwargs"
)


PATTERN_SIMPLE = re.compile(
    r"(?P<indent>^[ \t]*)(?P<assign>[\w\.]+)\s*=\s*snowflake\.connector\.connect\(\n"
    r"[ \t]*user=self\.config\.user,\n"
    r"[ \t]*password=self\.config\.password\.get_secret_value\(\),\n"
    r"[ \t]*account=self\.config\.account,\n"
    r"[ \t]*warehouse=self\.config\.warehouse,\n"
    r"[ \t]*database=self\.config\.database,\n"
    r"[ \t]*schema=self\.config\.schema_name,\n"
    r"[ \t]*role=self\.config\.role,\n"
    r"[ \t]*\)",
    re.MULTILINE,
)

PATTERN_SESSION = re.compile(
    r"(?P<indent>^[ \t]*)(?P<assign>self\._session_conn)\s*=\s*snowflake\.connector\.connect\(\n"
    r"[ \t]*user=self\.config\.user,\n"
    r"[ \t]*password=self\.config\.password\.get_secret_value\(\),\n"
    r"[ \t]*account=self\.config\.account,\n"
    r"[ \t]*warehouse=self\._resolve_warehouse\(operation\),\n"
    r"[ \t]*database=self\.config\.database,\n"
    r"[ \t]*schema=self\.config\.schema_name,\n"
    r"[ \t]*role=self\.config\.role,\n"
    r"[ \t]*session_parameters=\{\n"
    r"[ \t]*\"QUERY_TAG\":\s*self\.sf_behavior\.query_tag\s*or\s*\"Semabridge_Connector\"\n"
    r"[ \t]*\},\n"
    r"[ \t]*\)",
    re.MULTILINE,
)

PATTERN_PARAM = re.compile(
    r"(?P<indent>^[ \t]*)(?P<assign>[\w\.]+)\s*=\s*snowflake\.connector\.connect\(\n"
    r"[ \t]*user=self\.config\.user,\n"
    r"[ \t]*password=self\.config\.password\.get_secret_value\(\),\n"
    r"[ \t]*account=self\.config\.account,\n"
    r"[ \t]*warehouse=self\.config\.warehouse,\n"
    r"[ \t]*database=self\.config\.database,\n"
    r"[ \t]*schema=self\.config\.schema_name,\n"
    r"[ \t]*role=self\.config\.role,\n"
    r"[ \t]*session_parameters=\{\n"
    r"[ \t]*\"QUERY_TAG\":\s*(?P<tag>[^\n]+)\n"
    r"[ \t]*\},?\n"
    r"[ \t]*\)",
    re.MULTILINE,
)


def _ensure_helper_import(content: str) -> str:
    if HELPER_IMPORT in content:
        return content
    if "import snowflake.connector" in content:
        return content.replace(
            "import snowflake.connector",
            "import snowflake.connector\n" + HELPER_IMPORT,
            1,
        )
    return HELPER_IMPORT + "\n" + content


def _replace_simple(match: re.Match[str]) -> str:
    indent = match.group("indent")
    assign = match.group("assign")
    return (
        f"{indent}kw = get_snowflake_connect_kwargs(self.config)\n"
        f"{indent}{assign} = snowflake.connector.connect(**kw)"
    )


def _replace_session(match: re.Match[str]) -> str:
    indent = match.group("indent")
    assign = match.group("assign")
    return (
        f"{indent}kw = get_snowflake_connect_kwargs(self.config)\n"
        f"{indent}kw[\"warehouse\"] = self._resolve_warehouse(operation)\n"
        f"{indent}if \"session_parameters\" not in kw:\n"
        f"{indent}    kw[\"session_parameters\"] = {{}}\n"
        f"{indent}kw[\"session_parameters\"][\"QUERY_TAG\"] = "
        "self.sf_behavior.query_tag or \"Semabridge_Connector\"\n"
        f"{indent}{assign} = snowflake.connector.connect(**kw)"
    )


def _replace_param(match: re.Match[str]) -> str:
    indent = match.group("indent")
    assign = match.group("assign")
    tag_expr = match.group("tag").strip()
    return (
        f"{indent}kw = get_snowflake_connect_kwargs(self.config)\n"
        f"{indent}if \"session_parameters\" not in kw:\n"
        f"{indent}    kw[\"session_parameters\"] = {{}}\n"
        f"{indent}kw[\"session_parameters\"][\"QUERY_TAG\"] = {tag_expr}\n"
        f"{indent}{assign} = snowflake.connector.connect(**kw)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Snowflake connect-call refactor.")
    parser.add_argument(
        "--file",
        default="src/semabridge/connectors/snowflake_emitter.py",
        help="Target emitter file path.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show replacement counts without writing the file.",
    )
    args = parser.parse_args()

    target = Path(args.file)
    if not target.exists():
        print(f"Target file not found: {target}")
        return 1

    content = target.read_text(encoding="utf-8")
    content = _ensure_helper_import(content)

    content, simple_count = PATTERN_SIMPLE.subn(_replace_simple, content)
    content, session_count = PATTERN_SESSION.subn(_replace_session, content)
    content, param_count = PATTERN_PARAM.subn(_replace_param, content)
    remaining = content.count(".get_secret_value()")

    print(
        "Replacements -> "
        f"simple: {simple_count}, "
        f"session: {session_count}, "
        f"parameterized: {param_count}, "
        f"remaining .get_secret_value(): {remaining}"
    )

    if args.dry_run:
        return 0

    target.write_text(content, encoding="utf-8")
    print(f"Updated file: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
