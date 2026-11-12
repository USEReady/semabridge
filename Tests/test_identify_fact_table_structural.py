"""E10: the enrichment/time-flag fact table is chosen from active N:1 structure, never names."""
from __future__ import annotations

from types import SimpleNamespace as NS

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig


def _emitter():
    cfg = SnowflakeConfig(account="a.local", user="u", password="p", warehouse="w", database="d", schema_name="s")
    return SnowflakeEmitter(cfg, ConnectorBehavior())


def _ds(name):
    return NS(unique_name=name, columns=[NS(unique_name="c")])


def test_name_keyword_does_not_win_over_structure():
    model = NS(
        datasets=[_ds("SalesTargets"), _ds("Orders"), _ds("Customer")],
        relationships=[NS(from_dataset="Orders", to_dataset="Customer", is_active=True, cardinality="many_to_one")],
        metrics=[NS(dataset="SalesTargets"), NS(dataset="Orders")],
    )
    assert _emitter()._identify_fact_table(model) == "Orders"


def test_inactive_and_one_to_one_relationships_do_not_count():
    model = NS(
        datasets=[_ds("A"), _ds("B"), _ds("C")],
        relationships=[
            NS(from_dataset="A", to_dataset="C", is_active=False, cardinality="many_to_one"),
            NS(from_dataset="A", to_dataset="B", is_active=True, cardinality="one_to_one"),
            NS(from_dataset="B", to_dataset="C", is_active=True, cardinality="many_to_one"),
        ],
        metrics=[NS(dataset="A"), NS(dataset="A")],
    )
    assert _emitter()._identify_fact_table(model) == "B"


def test_one_to_many_counts_from_many_end_and_metrics_break_ties():
    model = NS(
        datasets=[_ds("Dim"), _ds("F1"), _ds("F2")],
        relationships=[
            NS(from_dataset="Dim", to_dataset="F1", is_active=True, cardinality="one_to_many"),
            NS(from_dataset="F2", to_dataset="Dim", is_active=True, cardinality="many_to_one"),
        ],
        metrics=[NS(dataset="F2"), NS(dataset="F2"), NS(dataset="F1")],
    )
    assert _emitter()._identify_fact_table(model) == "F2"


def test_no_relationships_falls_back_to_most_metrics_else_none():
    model = NS(datasets=[_ds("X"), _ds("Y")], relationships=[], metrics=[NS(dataset="Y")])
    assert _emitter()._identify_fact_table(model) == "Y"
    assert _emitter()._identify_fact_table(NS(datasets=[_ds("X")], relationships=[], metrics=[])) is None
