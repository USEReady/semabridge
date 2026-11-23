"""SnowflakeRules must equal what the Snowflake path does today.

Each value in semabridge.target_rules.snowflake is checked against the live
code that currently hard-codes it, or against the golden output
(Tests/golden), so moving shared code onto the rules later cannot change
Snowflake's output unnoticed.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import pytest

from semabridge.target_rules import (
    SNOWFLAKE_RULES,
    ReasonCode,
    UnsupportedTargetError,
    get_target_rules,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_METRICS = REPO_ROOT / "Tests" / "golden" / "fixtures" / "competitive_marketing" / "metrics.json"

DIALECT = SNOWFLAKE_RULES.dialect
POLICIES = SNOWFLAKE_RULES.policies


# --- lookup -----------------------------------------------------------------

def test_lookup_is_case_insensitive() -> None:
    assert get_target_rules("snowflake") is SNOWFLAKE_RULES
    assert get_target_rules("  Snowflake ") is SNOWFLAKE_RULES


def test_unknown_target_fails_clearly() -> None:
    with pytest.raises(UnsupportedTargetError, match="no-such-platform"):
        get_target_rules("no-such-platform")


def test_rules_are_read_only() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        POLICIES.placeholder_sql = "NULL"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        SNOWFLAKE_RULES.target = "other"  # type: ignore[misc]


def test_importing_rules_does_not_load_the_dax_parser() -> None:
    # The renderer will import the rules (Phase 2b); the rules must not
    # import it back. Checked in a fresh interpreter.
    code = (
        "import sys, semabridge.target_rules; "
        "bad = [m for m in ('semabridge.converter.dax_ast_parser', 'semabridge.dax_translation') "
        "if m in sys.modules]; "
        "print(','.join(bad))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        check=True,
    )
    assert result.stdout.strip() == ""


# --- dialect: matches the DAX renderer's output today -----------------------

def test_dialect_name_matches_tier5_dialect_enum() -> None:
    from semabridge.dax_translation.types import Dialect

    assert DIALECT.name == Dialect.SNOWFLAKE.value


@pytest.mark.parametrize(
    ("dax", "alias", "column"),
    [
        ("SUM(Sales[Sales Amount])", "SALES", "Sales Amount"),
        ("SUM(Budget[Budget Amount])", "BUDGET", "Budget Amount"),
        ("SUM('Sales'[Margin %])", "SALES", "Margin %"),
    ],
)
def test_column_ref_and_cast_match_renderer(dax: str, alias: str, column: str) -> None:
    from semabridge.converter.dax_ast_parser import try_ast_translate

    rendered = try_ast_translate(dax, table_alias=alias)
    expected = f"SUM({DIALECT.cast_float(DIALECT.column_ref(alias, column))})"
    assert rendered == expected


def test_column_ref_spelling() -> None:
    assert DIALECT.column_ref("SALES", "Sales Amount") == 'SALES."SALES_AMOUNT"'
    assert DIALECT.column_ref("SALES", "Margin %") == 'SALES."MARGIN_PCT"'


def test_table_alias_uses_todays_helper() -> None:
    from semabridge.utils.naming import to_alias

    for name in ("Sales", "Budget", "Product Category", "Date"):
        assert DIALECT.table_alias(name) == to_alias(name)


def test_identifier_and_cast_spelling() -> None:
    assert DIALECT.identifier_quote == '"'
    assert DIALECT.quote_identifier("FLAG_YTD") == '"FLAG_YTD"'
    assert DIALECT.qualify("SALES", "FLAG_YTD") == 'SALES."FLAG_YTD"'
    assert DIALECT.float_cast_suffix == "::FLOAT"
    assert DIALECT.cast_float("x") == "x::FLOAT"


def test_conversion_spelling() -> None:
    assert DIALECT.iff_function == "IFF"
    assert DIALECT.try_to_number("x") == "TRY_TO_NUMBER(x)"
    assert DIALECT.to_varchar("x") == "TO_VARCHAR(x)"


def test_date_spelling() -> None:
    assert DIALECT.current_date() == "CURRENT_DATE()"
    assert DIALECT.current_timestamp() == "CURRENT_TIMESTAMP()"
    assert DIALECT.date_add("MONTH", "-1", "d") == "DATEADD(MONTH, -1, d)"
    assert DIALECT.date_add("month", "-1", "d") == "DATEADD(month, -1, d)"
    assert DIALECT.date_trunc("YEAR", "d") == "DATE_TRUNC('YEAR', d)"
    assert DIALECT.last_day("d", "YEAR") == "LAST_DAY(d, 'YEAR')"
    assert DIALECT.date_diff("DAY", "a", "b") == "DATEDIFF(DAY, a, b)"


def test_iff_spelling_matches_renderer_divide() -> None:
    # The renderer's DIVIDE with an alternate result uses IFF.
    from semabridge.converter.dax_ast_parser import try_ast_translate

    rendered = try_ast_translate("DIVIDE(SUM(Sales[A]), SUM(Sales[B]), 0)", table_alias="SALES")
    assert rendered is not None and "IFF(" in rendered
    assert DIALECT.iff("x = 0", "0", "y") == "IFF(x = 0, 0, y)"


# --- policies: match today's constants and decline wording ------------------

def test_placeholder_matches_existing_constants() -> None:
    from semabridge.connectors.metrics_clause_builder import NULL_PLACEHOLDER_SQL
    from semabridge.connectors.snowflake_emitter_parts.semantic_view_items import NULL_METRIC_SQL

    assert POLICIES.placeholder_sql == NULL_PLACEHOLDER_SQL == NULL_METRIC_SQL


def test_by_design_prefix_matches_parser_constant() -> None:
    from semabridge.converter.dax_ast_parser import BY_DESIGN_EXCLUDED_PREFIX

    assert POLICIES.by_design_prefix == BY_DESIGN_EXCLUDED_PREFIX


def _golden_reasons() -> dict:
    data = json.loads(GOLDEN_METRICS.read_text(encoding="utf-8"))
    return {m["name"]: m["sync_failure_reason"] for m in data["metrics"]}


def test_text_measure_reason_matches_golden_output() -> None:
    # KPI01 is a text measure in the Competitive Marketing model.
    assert _golden_reasons()["KPI01"] == POLICIES.decide(ReasonCode.STRING_FUNCTION_ROOT).reason


def test_constant_measure_reason_matches_golden_output() -> None:
    # #Space01 is a constant measure in the Competitive Marketing model.
    assert _golden_reasons()["#Space01"] == POLICIES.decide(ReasonCode.CONSTANT_EXPRESSION).reason


def test_snowflake_policy_choices() -> None:
    assert POLICIES.decline_text_measures is True
    assert POLICIES.decline_constant_measures is True
    assert POLICIES.decline_two_table_measures is True


def test_snowflake_capabilities() -> None:
    caps = SNOWFLAKE_RULES.capabilities
    assert caps.text_measures is False
    assert caps.window_metrics is True
    assert caps.bridge_views is False
