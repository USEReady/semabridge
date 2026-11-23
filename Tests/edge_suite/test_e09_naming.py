"""Deployed identifiers: always valid, stable, and distinct for distinct Power BI names."""
import re

import pytest

from semabridge.utils.name_translator import get_target_deployment_name as name

VALID = re.compile(r'^(?:[A-Za-z_][A-Za-z0-9_$]*|"[^"]+")$')
INPUTS = ["Total Units", "Revenue YTD", "Cases MoM%", "Close %", "CSAT Impact - Products", "Rev Goal", "a", "A",
          "Sales 2024", "2024 Sales", "% Growth", "Year", "Order", "Select", "Table", "Date", "Group", "Ünïcödé Name",
          "Kosten (€)", "日本語", "x" * 300, "  padded  ", "Tab\tName", "New\nLine", "Quote\"Name", "Apos'Name",
          "Dash-Name", "Dot.Name", "Slash/Name", "Back\\Name", "Amp & Name", "Plus+Name", "Hash#Name", "At@Name",
          "Paren (x)", "Bracket [x]", "Emoji 📈", "_lead", "trail_", "MiXeD CaSe"]


@pytest.mark.parametrize("raw", INPUTS)
@pytest.mark.parametrize("platform", ["snowflake", "databricks"])
def test_identifier_is_valid_and_stable(raw, platform):
    out = name(raw, platform)
    assert out and VALID.match(out), repr(out)
    assert name(raw, platform) == out
    assert len(out) <= 255


DISTINCT = [("RunningMonths", "Running Months"), ("Close %", "Close"), ("Cases MoM%", "Cases MoM"),
            ("Revenue YTD", "Revenue  YTD x"), ("A-B", "A B x"), ("Sales 2024", "Sales 2025"),
            ("Margin %", "Margin"), ("Cost (€)", "Cost ($)"), ("日本", "中国"), ("Year", "Year 1")]


@pytest.mark.parametrize("a,b", DISTINCT)
def test_distinct_names_get_distinct_identifiers(a, b):
    assert name(a, "snowflake") != name(b, "snowflake")
