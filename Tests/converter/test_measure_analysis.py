"""Understand measures (converter/measure_analysis.py): facts + graph.

Each fact on small DAX examples, the dependency graph (order, cycles,
dependents, tables used through references), and the plan doc's running
example. That the facts match what osi_to_sml.py decides on real models is
checked in Tests/golden/test_snowflake_golden.py.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from semabridge.converter.measure_analysis import (
    analyze_measure,
    analyze_measures,
    implicit_dax,
    is_sql_sourced,
    missing_model_column_refs,
    model_column_index,
)


def _table(name, *columns):
    return NS(unique_name=name, columns=[NS(unique_name=c) for c in columns])


def _measure(name, dataset, expression="", **extra):
    return NS(unique_name=name, dataset=dataset, expression=expression, **extra)


def _model(*metrics, tables=None):
    return NS(
        datasets=tables
        or [
            _table("Sales", "Sales Amount", "ProductKey", "Amount", "Status"),
            _table("Budget", "Budget Amount", "ProductKey"),
            _table("Product", "ProductKey", "CategoryKey", "Color"),
            _table("Category", "CategoryKey", "Name"),
            _table("Sales Table", "Qty"),
        ],
        metrics=list(metrics),
    )


# --- the plan doc's running example ------------------------------------------

@pytest.fixture(scope="module")
def running_example():
    return analyze_measures(
        _model(
            _measure("Total Sales", "Sales", "SUM(Sales[Sales Amount])"),
            _measure("Total Budget", "Budget", "SUM(Budget[Budget Amount])"),
            _measure("Sales vs Budget %", "Sales", "DIVIDE([Total Sales], [Total Budget])"),
            _measure("Sales Label", "Sales", '"Sales: " & FORMAT([Total Sales], "0")'),
        )
    )


def test_running_example_facts(running_example):
    m = running_example.measures
    assert m["Total Sales"].references == ()
    assert m["Total Sales"].tables_used == ("Sales",)
    assert m["Total Budget"].tables_used == ("Budget",)
    assert m["Sales vs Budget %"].references == ("Total Budget", "Total Sales")
    assert m["Sales Label"].references == ("Total Sales",)
    assert m["Sales Label"].is_text_result is True
    # Today's narrower Snowflake check does not see a root `&`.
    assert m["Sales Label"].root_is_string_function is False
    assert m["Total Sales"].complexity["tier"] == 1


def test_running_example_graph(running_example):
    g = running_example.graph
    assert g.order == ("Total Sales", "Total Budget", "Sales vs Budget %", "Sales Label")
    assert g.cycles == ()
    assert g.dependents["Total Sales"] == ("Sales vs Budget %", "Sales Label")
    assert g.dependents["Total Budget"] == ("Sales vs Budget %",)
    # The two-fact measure a bridge view is needed for.
    assert g.all_tables_used["Sales vs Budget %"] == ("Sales", "Budget")
    assert g.all_tables_used["Sales Label"] == ("Sales",)


# --- one fact at a time -------------------------------------------------------

def test_constant_measure():
    # The real shape in Competitive Marketing: a blank text measure.
    a = analyze_measures(_model(_measure("Space", "Sales", '""'))).measures["Space"]
    assert a.is_constant is True and a.tables_used == ()


def test_numeric_literal_is_not_constant():
    # Existing rule: a bare number is a translatable constant metric
    # (rendered MAX(<literal>)), so it is not "constant" in this sense.
    a = analyze_measures(_model(_measure("Five", "Sales", "5"))).measures["Five"]
    assert a.is_constant is False


def test_string_function_root():
    a = analyze_measures(_model(_measure("Fmt", "Sales", 'FORMAT(SUM(Sales[Amount]), "0")'))).measures["Fmt"]
    assert a.root_is_string_function is True and a.is_text_result is True


def test_numeric_measure_is_not_text():
    a = analyze_measures(_model(_measure("N", "Sales", "SUM(Sales[Amount]) * 2"))).measures["N"]
    assert a.is_text_result is False and a.root_is_string_function is False and a.is_constant is False


def test_text_through_var_body():
    a = analyze_measures(_model(_measure("T", "Sales", 'VAR x = SUM(Sales[Amount]) RETURN "n=" & x'))).measures["T"]
    assert a.is_text_result is True


def test_implicit_measure_builds_dax():
    m = _measure("Amount Sum", "Sales", "", source_column="Amount", aggregation=NS(name="SUM"))
    a = analyze_measures(_model(m)).measures["Amount Sum"]
    assert a.implicit is True and a.dax == "SUM('Sales'[Amount])" and a.tables_used == ("Sales",)
    assert implicit_dax(m) == "SUM('Sales'[Amount])"


@pytest.mark.parametrize(
    ("agg", "fn"),
    [("AVG", "AVERAGE"), ("MIN", "MIN"), ("MAX", "MAX"), ("COUNT", "COUNT"), ("COUNT_DISTINCT", "DISTINCTCOUNT")],
)
def test_implicit_aggregations(agg, fn):
    m = _measure("X", "Sales", "", source_column="Amount", aggregation=NS(name=agg))
    assert implicit_dax(m) == f"{fn}('Sales'[Amount])"


def test_empty_measure():
    a = analyze_measures(_model(_measure("Empty", "Sales", ""))).measures["Empty"]
    assert a.is_empty is True and a.complexity is None and a.references == ()


def test_sql_sourced_measure_skips_dax_facts():
    m = _measure("FromSf", "Sales", "SUM(AMOUNT)", sql_expression="SUM(AMOUNT)")
    assert is_sql_sourced(m) is True
    a = analyze_measures(_model(m)).measures["FromSf"]
    assert a.sql_sourced is True and a.complexity is None and a.is_text_result is False


def test_separate_dax_is_not_sql_sourced():
    m = _measure("Both", "Sales", "SUM(Sales[Amount])", sql_expression="SUM(SALES.AMOUNT)")
    assert is_sql_sourced(m) is False


def test_missing_column_is_reported_and_not_analysed_further():
    model = _model(_measure("Broken", "Sales", "SUM(Sales[Gone])"))
    a = analyze_measures(model).measures["Broken"]
    assert a.missing_column_refs == ("Sales[Gone]",)
    assert a.complexity is None and a.is_constant is False
    assert missing_model_column_refs("SUM(Sales[Gone])", model_column_index(model), []) == ["Sales[Gone]"]


def test_missing_column_ignores_strings_comments_and_unknown_tables():
    cols = model_column_index(_model())
    assert missing_model_column_refs('"Sales[Gone]" // Sales[Gone]', cols, []) == []
    assert missing_model_column_refs("SUM(Virtual[Gone])", cols, []) == []


def test_tables_used_variants():
    a = analyze_measures(
        _model(
            _measure("Q", "Sales", "COUNTROWS('Sales Table')"),
            _measure("U", "Sales", "SUM([Amount])"),
            _measure("F", "Sales", 'CALCULATE(SUM(Sales[Amount]), FILTER(ALL(Product), Product[Color] = "Red"))'),
            _measure("V", "Sales", "VAR Budget = 1 RETURN SUM(Sales[Amount]) + Budget"),
        )
    ).measures
    assert a["Q"].tables_used == ("Sales Table",)
    assert a["U"].tables_used == ("Sales",)  # unqualified [Amount] is a column of the home table
    assert a["F"].tables_used == ("Sales", "Product")
    # A table name used as an identifier counts (here a VAR shares a table name).
    assert "Sales" in a["V"].tables_used


# --- graph ----------------------------------------------------------------------

def test_cycle_and_self_reference():
    g = analyze_measures(
        _model(
            _measure("A", "Sales", "[B] + 1"),
            _measure("B", "Sales", "[A] * 2"),
            _measure("C", "Sales", "[C] + 1"),
            _measure("D", "Sales", "SUM(Sales[Amount])"),
        )
    ).graph
    assert g.cycles == (("A", "B", "A"), ("C", "C"))
    assert g.in_cycle("A") and g.in_cycle("C") and not g.in_cycle("D")
    assert set(g.order) == {"A", "B", "C", "D"} and len(g.order) == 4


def test_chain_order_ignores_source_order():
    g = analyze_measures(
        _model(
            _measure("Top", "Sales", "[Mid] * 2"),
            _measure("Mid", "Sales", "[Base] + 1"),
            _measure("Base", "Sales", "SUM(Sales[Amount])"),
        )
    ).graph
    assert g.order == ("Base", "Mid", "Top")
    assert g.all_tables_used["Top"] == ("Sales",)


def test_measure_depending_on_a_cycle_comes_after_it():
    g = analyze_measures(
        _model(
            _measure("User", "Sales", "[A] + [B]"),
            _measure("A", "Sales", "[B]"),
            _measure("B", "Sales", "[A]"),
        )
    ).graph
    assert g.order.index("User") > g.order.index("A") and g.order.index("User") > g.order.index("B")


def test_names_match_case_insensitively_and_by_original_name():
    a = analyze_measures(
        _model(
            _measure("Total Sales", "Sales", "SUM(Sales[Amount])", original_name="Sales Total"),
            _measure("Lower", "Sales", "[total sales] * 2"),
            _measure("Orig", "Sales", "[Sales Total] + 1"),
        )
    )
    assert a.measures["Lower"].references == ("Total Sales",)
    assert a.measures["Orig"].references == ("Total Sales",)
    assert a.graph.dependents["Total Sales"] == ("Lower", "Orig")


def test_unqualified_column_is_not_a_reference():
    a = analyze_measures(_model(_measure("U", "Sales", "SUM([Amount])"))).measures["U"]
    assert a.references == ()
    # As written it is still reported, exactly as the converter saw it before.
    assert a.referenced_names == ("Amount",)


def test_per_metric_keeps_every_entry_in_order():
    a = analyze_measures(_model(_measure("X", "Sales", "1"), _measure("X", "Sales", "SUM(Sales[Amount])")))
    assert [m.dax for m in a.per_metric] == ["1", "SUM(Sales[Amount])"]
    assert a.measures["X"].dax == "1"  # first one wins by name


def test_results_are_read_only():
    a = analyze_measures(_model(_measure("A", "Sales", "SUM(Sales[Amount])"))).measures["A"]
    with pytest.raises(Exception):
        a.dax = "changed"  # type: ignore[misc]
    copy = a.complexity_copy()
    copy["depends_on_measures"].append("X")
    assert "X" not in a.complexity["depends_on_measures"]


def test_single_measure_without_model():
    a = analyze_measure(_measure("A", "Sales", "[B] + 1"), known_measure_names={"A", "B"})
    assert a.references == ("B",) and a.missing_column_refs == () and a.tables_used == ()


def test_analysis_is_platform_neutral():
    """No SQL, quoting or rules anywhere in the facts."""
    a = analyze_measures(_model(_measure("A", "Sales", "SUM(Sales[Sales Amount])"))).measures["A"]
    text = repr(a)
    assert "SALES_AMOUNT" not in text and '"SALES"' not in text and "::FLOAT" not in text
