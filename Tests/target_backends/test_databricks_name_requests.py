"""Databricks integration fixes, batch 1: saved names as planner requests,
security-role states, and the run-side collection of saved names.

Precedence (per metric view): deployed names > firm requests (explicit user
edits; a conflict is BLOCKING) > soft requests (unmarked historical names;
never displace another object's natural name, changes are reported) >
automatic names. Display names stay the Power BI labels.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence

import pytest

from semabridge.sml.models import Cardinality, CrossFilterDirection, SMLColumn, SMLDataset, SMLMetric, SMLModel, SMLRelationship
from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views
from semabridge.target_backends.databricks.planner.model import SourceId


# --- model builders (same shapes as test_databricks_planner.py) --------------------------

def col(name: str, hidden: bool = False, label: Optional[str] = None) -> SMLColumn:
    return SMLColumn(unique_name=name, label=label or name, is_hidden=hidden)


def table(name: str, *cols, fact: bool = False) -> SMLDataset:
    return SMLDataset(unique_name=name, label=name, columns=[c if isinstance(c, SMLColumn) else col(c) for c in cols],
                      is_fact=fact)


def rel(many: str, many_col: str, one: str, one_col: str) -> SMLRelationship:
    return SMLRelationship(unique_name=f"{many}_{one}_{many_col}", from_dataset=many, from_columns=[many_col],
                           to_dataset=one, to_columns=[one_col], cardinality=Cardinality("many-to-one"),
                           is_active=True, cross_filter=CrossFilterDirection.SINGLE)


def measure(name: str, home: str, dax: str, sql: str, label: Optional[str] = None) -> SMLMetric:
    return SMLMetric(unique_name=name, label=label or name, dataset=home, expression=dax, sql_expression=sql,
                     sync_enabled=True)


REVENUE = measure("Total Revenue", "Sales", "SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`::DOUBLE)")
CAPACITY = measure("Total Capacity", "Store", "SUM(Store[Capacity])", "SUM(STORE.`CAPACITY`::DOUBLE)")


def store_sales(measures: Sequence[SMLMetric] = (REVENUE,)) -> SMLModel:
    return SMLModel(
        unique_name="m",
        datasets=[table("Store", "StoreID", col("StoreName", label="Store Name"), "Capacity", col("Secret", hidden=True)),
                  table("Sales", "SaleID", "StoreID", "ProductID", "Amount", "Qty", fact=True),
                  table("Product", "ProductID", "Category", "Price")],
        relationships=[rel("Sales", "StoreID", "Store", "StoreID"), rel("Sales", "ProductID", "Product", "ProductID")],
        metrics=list(measures),
    )


def plan(model: SMLModel, requests: Sequence[tuple] = (), roles: Optional[int] = 0) -> Dict:
    return plan_metric_views(model, None, PlannerSettings(requested_names=tuple(requests), security_roles=roles)).as_dict()


def view(p: Dict, name: str) -> Dict:
    return next(v for v in p["views"] if v["name"] == name)


def field(v: Dict, dataset: str, column: str) -> Dict:
    return next(f for f in v["fields"] if f["dataset"] == dataset and f["column"] == column)


def outcome(p: Dict, path: str) -> Dict:
    return next(r for r in p["name_requests"] if r["source_path"] == path)


def name_blockers(p: Dict) -> List[Dict]:
    return [b for b in p["blockers"] if b["code"] == "name_override_conflict"]


# --- firm requests (explicit user edits) ------------------------------------------------------

def test_firm_field_rename_changes_the_name_and_keeps_the_power_bi_label():
    p = plan(store_sales(), [("datasets.Store.columns.StoreName", "shop_name", "user")])
    f = field(view(p, "sales_metrics"), "Store", "StoreName")
    assert f["name"] == "shop_name"
    assert f["display_name"] == "Store Name"          # the Power BI label, not the rename
    assert "Store Name" in f["synonyms"]
    o = outcome(p, "datasets.Store.columns.StoreName")
    assert (o["status"], o["origin"]) == ("kept", "user")
    assert name_blockers(p) == []
    assert not any(r["final"] == "shop_name" for r in p["renames"])   # a user name is not an automatic rename


def test_firm_measure_rename_keeps_label_and_sql():
    before = plan(store_sales())
    p = plan(store_sales(), [("metrics.Total Revenue", "revenue", "user")])
    m = view(p, "sales_metrics")["measures"][0]
    assert (m["name"], m["display_name"], m["source_name"]) == ("revenue", "Total Revenue", "Total Revenue")
    assert m["expr"] == view(before, "sales_metrics")["measures"][0]["expr"]


def test_firm_request_takes_another_objects_natural_name():
    p = plan(store_sales(), [("datasets.Sales.columns.Qty", "amount", "user")])
    v = view(p, "sales_metrics")
    assert field(v, "Sales", "Qty")["name"] == "amount"
    assert field(v, "Sales", "Amount")["name"] == "sales_amount"     # automatic name moves aside
    assert name_blockers(p) == []


def test_two_firm_requests_for_one_name_block_publication():
    p = plan(store_sales(), [("datasets.Sales.columns.Amount", "value", "user"),
                             ("datasets.Product.columns.Price", "value", "user")])
    blockers = name_blockers(p)
    assert len(blockers) == 1
    b = blockers[0]
    assert b["view"] == "sales_metrics" and b["source_path"] == "datasets.Product.columns.Price"
    assert b["details"]["requested"] == "value" and "Amount" in b["details"]["holder"]
    assert p["summary"]["publication_blocked"] is True
    assert outcome(p, "datasets.Product.columns.Price")["status"] == "conflict"
    # the plan stays a valid preview: the conflicting field still has a unique name
    names = [f["name"] for f in view(p, "sales_metrics")["fields"]]
    assert len(names) == len(set(names))


def test_firm_request_for_a_join_alias_is_blocking():
    p = plan(store_sales(), [("datasets.Sales.columns.Qty", "store", "user")])
    (b,) = name_blockers(p)
    assert "join alias" in b["details"]["holder"]


def test_firm_request_is_checked_in_every_view_and_conflicts_name_the_view():
    m = store_sales([REVENUE, CAPACITY])
    ok = plan(m, [("datasets.Store.columns.StoreName", "shop_name", "user")])
    o = outcome(ok, "datasets.Store.columns.StoreName")
    assert sorted(v["view"] for v in o["views"]) == ["sales_metrics", "store_metrics"]
    assert all(v["final"] == "shop_name" for v in o["views"])

    clash = plan(m, [("datasets.Store.columns.StoreName", "shop_name", "user"),
                     ("datasets.Sales.columns.Qty", "shop_name", "user")])
    o = outcome(clash, "datasets.Store.columns.StoreName")
    assert o["status"] == "conflict"
    by_view = {v["view"]: v["status"] for v in o["views"]}
    assert by_view == {"sales_metrics": "conflict", "store_metrics": "kept"}
    assert [b["view"] for b in name_blockers(clash)] == ["sales_metrics"]


# --- soft requests (unmarked, historical) ---------------------------------------------------

def test_soft_request_is_kept_when_free():
    p = plan(store_sales(), [("datasets.Sales.columns.Qty", "units", None)])
    assert field(view(p, "sales_metrics"), "Sales", "Qty")["name"] == "units"
    o = outcome(p, "datasets.Sales.columns.Qty")
    assert (o["status"], o["origin"]) == ("kept", "unmarked")


def test_soft_request_never_displaces_another_natural_name_and_is_reported():
    p = plan(store_sales(), [("datasets.Sales.columns.Qty", "amount", None)])
    v = view(p, "sales_metrics")
    assert field(v, "Sales", "Amount")["name"] == "amount"
    assert field(v, "Sales", "Qty")["name"] == "qty"
    o = outcome(p, "datasets.Sales.columns.Qty")
    assert o["status"] == "changed" and "natural name" in o["reason"]
    assert p["blockers"] == []                      # soft: reported, not blocking


def test_soft_request_loses_to_a_firm_request():
    p = plan(store_sales(), [("datasets.Sales.columns.Qty", "units", "user"),
                             ("datasets.Sales.columns.Amount", "units", None)])
    v = view(p, "sales_metrics")
    assert field(v, "Sales", "Qty")["name"] == "units"
    assert outcome(p, "datasets.Sales.columns.Amount")["status"] == "changed"
    assert name_blockers(p) == []


# --- requests that match nothing -------------------------------------------------------------

def test_unknown_and_not_placed_requests_are_reported_not_dropped():
    p = plan(store_sales(), [("datasets.Nope.columns.X", "x", "user"),
                             ("datasets.Sales", "sales_view", "user"),
                             ("datasets.Store.columns.Secret", "secret_name", "user")])
    assert outcome(p, "datasets.Nope.columns.X")["status"] == "not_applicable"
    assert outcome(p, "datasets.Sales")["status"] == "not_applicable"
    assert outcome(p, "datasets.Store.columns.Secret")["status"] == "not_placed"
    assert name_blockers(p) == []


def test_requests_match_case_insensitively_when_unique():
    p = plan(store_sales(), [("datasets.sales.columns.qty", "units", "user")])
    assert field(view(p, "sales_metrics"), "Sales", "Qty")["name"] == "units"


def test_no_requests_plan_is_unchanged():
    a, b = plan(store_sales()), plan(store_sales(), [])
    assert a["views"] == b["views"] and a["name_requests"] == [] and b["blockers"] == []


# --- security roles ----------------------------------------------------------------------------

@pytest.mark.parametrize("roles, code", [(None, "security_roles_unknown"), (2, "security_roles_present"), (0, None)])
def test_security_role_states_are_structured_blockers(roles, code):
    p = plan(store_sales(), roles=roles)
    codes = [b["code"] for b in p["blockers"]]
    assert codes == ([code] if code else [])
    assert p["summary"]["publication_blocked"] is bool(code)
    if roles:
        assert p["blockers"][0]["details"]["security_roles"] == roles


def test_default_settings_treat_roles_as_unknown():
    p = plan_metric_views(store_sales(), None, PlannerSettings()).as_dict()
    assert [b["code"] for b in p["blockers"]] == ["security_roles_unknown"]


def _ctx(source="pbix", roles=None, tmsl=None, parser=None):
    osi = SimpleNamespace(metadata={"security_roles": roles} if roles is not None else {})
    return SimpleNamespace(source_type=source, osi_model=osi, pbix_parser=parser,
                           source_format=SimpleNamespace(tmsl_definition=tmsl))


def test_security_roles_for_run_three_states():
    from semabridge.core.engine.targets.databricks import security_roles_for_run

    assert security_roles_for_run(_ctx(roles=[{"name": "East"}], tmsl={"model": {}})) == 1
    assert security_roles_for_run(_ctx(tmsl={"model": {"tables": []}}, parser="schema")) == 0
    assert security_roles_for_run(_ctx(tmsl={"model": {"tables": []}}, parser="pbixray")) is None   # not confirmed
    assert security_roles_for_run(_ctx(tmsl={"model": {"roles": []}}, parser="pbixray")) == 0       # table read, empty
    assert security_roles_for_run(_ctx(tmsl={"model": {"tables": []}}, parser="extract")) is None
    assert security_roles_for_run(_ctx(tmsl=None)) is None                                         # nothing to read
    assert security_roles_for_run(_ctx(source="fabric", tmsl={"model": {}})) == 0
    assert security_roles_for_run(_ctx(source="snowflake")) == 0
    assert security_roles_for_run(_ctx(source="unknown")) is None


# --- run side: saved names are collected, the model is not touched --------------------------

def _run_ctx(target: str, planner: bool, payload: dict):
    return SimpleNamespace(
        target_type=target, config_path=Path("p.yaml"), config_payload=payload, databricks_name_requests=None,
        behavior=SimpleNamespace(databricks=SimpleNamespace(use_planner_path=planner)),
    )


def _engine():
    from semabridge.core.engine import config as cfg

    return SimpleNamespace(_apply_mapping_overrides_from_config=cfg._apply_mapping_overrides_from_config), cfg


def _osi_like():
    return store_sales([measure("Total Revenue", "Sales", "SUM(Sales[Amount])", "x"),
                        measure("Revenue LY", "Sales", "CALCULATE([Total Revenue], SAMEPERIODLASTYEAR('D'[D]))", "y")])


PAYLOAD = {
    "target": {"type": "databricks"},
    "mappings_overrides": [
        {"source_path": "metrics.Total Revenue", "target_name": "Revenue", "origin": "user"},
        {"source_path": "datasets.Store.columns.StoreName", "target_name": "STORE_NAME"},
    ],
}


def test_databricks_planner_run_collects_requests_and_leaves_model_untouched():
    engine, cfg = _engine()
    m = _osi_like()
    before = m.model_dump()
    ctx = _run_ctx("databricks", True, PAYLOAD)
    cfg._apply_overrides_for_run(engine, ctx, m)
    assert m.model_dump() == before                               # labels, names, DAX unchanged (same object)
    reqs = {r["source_path"]: r for r in ctx.databricks_name_requests}
    assert reqs["metrics.Total Revenue"]["target_name"] == "revenue" and reqs["metrics.Total Revenue"]["origin"] == "user"
    assert reqs["datasets.Store.columns.StoreName"]["target_name"] == "store_name"
    assert reqs["datasets.Store.columns.StoreName"]["origin"] is None   # unmarked -> soft


def test_requests_reach_planner_settings():
    from semabridge.core.engine.targets.databricks import databricks_planner_settings

    ctx = SimpleNamespace(
        behavior=SimpleNamespace(databricks=SimpleNamespace(planner_calculate_mode="approximate",
                                                            planner_view_mode="separate_views", planner_capabilities=[])),
        config=SimpleNamespace(target=SimpleNamespace()),
        databricks_name_requests=[{"source_path": "metrics.Total Revenue", "target_name": "revenue", "origin": "user"}],
        source_type="pbix", osi_model=SimpleNamespace(metadata={}), pbix_parser="schema",
        source_format=SimpleNamespace(tmsl_definition={"model": {"tables": []}}),
    )
    s = databricks_planner_settings(ctx)
    assert s.requested_names == (("metrics.Total Revenue", "revenue", "user"),)
    assert s.security_roles == 0


@pytest.mark.parametrize("target, planner", [("snowflake", False), ("databricks", False), ("fabric", False), ("", False)])
def test_other_runs_still_apply_overrides_to_the_model(target, planner):
    """Snowflake, Fabric, no target and the old Databricks publisher path keep
    the previous behaviour exactly (the same function is called)."""
    engine, cfg = _engine()
    payload = {"target": {"type": target} if target else {},
               "mappings_overrides": [{"source_path": "metrics.Total Revenue", "target_name": "REVENUE"}]}
    a, b = _osi_like(), _osi_like()
    cfg._apply_overrides_for_run(engine, _run_ctx(target, planner, payload), a)
    cfg._apply_mapping_overrides_from_config(b, Path("p.yaml"), config_payload=payload)
    assert a.model_dump(exclude={"created_at"}) == b.model_dump(exclude={"created_at"})
    assert a.metrics[0].unique_name != "Total Revenue"            # the old path did rename it


def test_full_planner_run_with_saved_names_end_to_end():
    """Saved names -> collected -> settings -> plan: firm kept, soft reported."""
    engine, cfg = _engine()
    m = store_sales([REVENUE])
    ctx = _run_ctx("databricks", True, {"mappings_overrides": [
        {"source_path": "datasets.Sales.columns.Qty", "target_name": "Units", "origin": "user"},
        {"source_path": "datasets.Sales.columns.Price", "target_name": "x"},
        {"source_path": "datasets.Store.columns.StoreName", "target_name": "amount"},
    ]})
    cfg._apply_overrides_for_run(engine, ctx, m)
    settings = PlannerSettings(security_roles=0, requested_names=tuple(
        (r["source_path"], r["target_name"], r["origin"]) for r in ctx.databricks_name_requests))
    p = plan_metric_views(m, None, settings).as_dict()
    v = view(p, "sales_metrics")
    assert field(v, "Sales", "Qty")["name"] == "units"
    assert field(v, "Store", "StoreName")["name"] == "store_name"
    assert outcome(p, "datasets.Store.columns.StoreName")["status"] == "changed"
    assert outcome(p, "datasets.Sales.columns.Price")["status"] == "not_applicable"
    assert p["blockers"] == []
