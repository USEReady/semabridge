"""Tests for shared DAX query construction and parsing."""
import pytest
from semabridge.ai.metric_execution.adapters.dax_shared import (
    build_dax_query,
    clean_measure_name,
    extract_single_value,
    normalize_dax_columns,
    normalize_measure_name,
)


def test_normalize_and_clean_measure_name():
    assert normalize_measure_name("Sales") == "[Sales]"
    assert normalize_measure_name("[Sales]") == "[Sales]"
    assert normalize_measure_name("'Finance'[Profit]") == "'Finance'[Profit]"
    assert clean_measure_name("[Total Revenue]") == "Total Revenue"
    assert clean_measure_name("'Table'[Metric]") == "Metric"


def test_build_dax_query_unsliced():
    q = build_dax_query("Total Profit")
    assert q == 'EVALUATE ROW("Value", [Total Profit])'


def test_build_dax_query_sliced():
    filters = {"Accounts.Account": "Corporate", "Date.Year": 2024}
    q = build_dax_query("CLE", filters)
    assert 'EVALUATE CALCULATETABLE(' in q
    assert 'ROW("Value", [CLE])' in q
    assert '[Accounts.Account] = "Corporate"' in q
    assert '[Date.Year] = 2024' in q


def test_normalize_dax_columns():
    cols = ["[Value]", "'Table'[Metric]", "ColA"]
    norm = normalize_dax_columns(cols)
    assert norm == ["Value", "Metric", "ColA"]


def test_extract_single_value():
    rows = [{"Value": 125000.50}]
    assert extract_single_value(rows, "Total Sales") == 125000.50

    rows_named = [{"[CLE]": 78.4}]
    assert extract_single_value(rows_named, "CLE") == 78.4

    empty_rows = []
    assert extract_single_value(empty_rows, "CLE") is None
