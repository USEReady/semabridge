"""Phase 3.7: the metric-view writer, bundles and proposed table locations.

The writer copies names and expressions from the plan exactly, writes YAML 1.1
with a serializer, and refuses (fails closed) instead of guessing. Bundles are
written atomically, one folder per plan revision; only the current bundle of
the current plan is deployable, and never a probe bundle.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from semabridge.sml.models import Cardinality, CrossFilterDirection, SMLColumn, SMLDataset, SMLMetric, SMLModel, SMLRelationship
from semabridge.target_backends.databricks import bundle
from semabridge.target_backends.databricks.planner import Capability, PlannerSettings, plan_metric_views
from semabridge.target_backends.databricks.tables import UcTable, plan_tables
from semabridge.target_backends.databricks.writer import (
    FIELDS_KEY,
    ProbeSources,
    WriterError,
    render_plan,
    table_reference,
    translate_format,
)

OUT = ("workspace", "semabridge_dev")


# --- model builders ------------------------------------------------------------------------

def col(name, label=None, desc="", fmt=None, hidden=False):
    return SMLColumn(unique_name=name, label=label or name, description=desc, format_string=fmt, is_hidden=hidden)


def table(name, *cols, fact=False):
    return SMLDataset(unique_name=name, label=name, columns=[c if isinstance(c, SMLColumn) else col(c) for c in cols],
                      is_fact=fact)


def rel(many, many_col, one, one_col):
    return SMLRelationship(unique_name=f"{many}_{one}", from_dataset=many, from_columns=[many_col], to_dataset=one,
                           to_columns=[one_col], cardinality=Cardinality("many-to-one"), is_active=True,
                           cross_filter=CrossFilterDirection.SINGLE)


def measure(name, dax, sql, desc="", fmt=None, flags=()):
    return SMLMetric(unique_name=name, label=name, dataset="Sales", expression=dax, sql_expression=sql,
                     sync_enabled=True, description=desc, format_string=fmt, advisory_categories=list(flags))


def chain_model(extra_measures=()):
    """Sales -> Store -> District (a nested many-to-one chain)."""
    return SMLModel(
        unique_name="m", label="Chain Model",
        datasets=[
            table("Sales", "SaleID", "StoreID", col("Amount", fmt="#,0.00"), fact=True),
            table("Store", "StoreID", col("Store Name", desc="Trading name of the store"), "DistrictID", "Capacity"),
            table("District", "DistrictID", col("District Name")),
        ],
        relationships=[rel("Sales", "StoreID", "Store", "StoreID"), rel("Store", "DistrictID", "District", "DistrictID")],
        metrics=[measure("Total Sales", "SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`::DOUBLE)",
                         desc="All sales", fmt="[$$-409]#,##0.00"),
                 measure("Sales LY", "CALCULATE([Total Sales], Store[Store Name] = \"A\")",
                         "SUM(CASE WHEN STORE.`STORE_NAME` = 'A' THEN SALES.`AMOUNT`::DOUBLE END)",
                         flags=["calculate_filter_semantics_difference"]),
                 *extra_measures],
    )


def stub_plan(model, settings=None, existing=frozenset()):
    tp = plan_tables(model, {OUT: {}}, source_schemas=[OUT], on_missing_source="stub")
    s = settings or PlannerSettings(security_roles=0, output_catalog=OUT[0], output_schema=OUT[1],
                                    existing_objects=frozenset(existing))
    plan = plan_metric_views(model, tp, s).as_dict()
    plan["meta"] = {"revision": 100, "status": "valid"}
    return plan


def render(plan, **kw):
    return render_plan(plan, catalog=OUT[0], schema=OUT[1], model_label="Chain Model", **kw)


def doc_of(rv):
    return yaml.safe_load(rv.yaml)


# --- mapping ---------------------------------------------------------------------------------

def test_view_text_follows_the_plan_exactly():
    plan = stub_plan(chain_model())
    (rv,) = render(plan)
    v = plan["views"][0]
    assert rv.view == v["name"] == "sales_metrics"
    assert rv.fqn == "`workspace`.`semabridge_dev`.`sales_metrics`"
    assert rv.create_sql.startswith("CREATE VIEW `workspace`.`semabridge_dev`.`sales_metrics` WITH METRICS LANGUAGE YAML AS $$\n")
    assert rv.create_sql.endswith("$$")
    assert rv.yaml.startswith("version: 1.1\n")
    d = doc_of(rv)
    assert d["version"] == 1.1 and d["source"] == "workspace.semabridge_dev.sales"
    # every table of this plan is a stub: the approved definition carries the INCOMPLETE warning (3.8)
    from semabridge.target_backends.databricks.writer import INCOMPLETE_WARNING

    assert d["comment"] == f"{INCOMPLETE_WARNING} Created by SemaBridge from model Chain Model"
    assert [f["name"] for f in d[FIELDS_KEY]] == [f["name"] for f in v["fields"]]
    assert [f["expr"] for f in d[FIELDS_KEY]] == [f["expr"] for f in v["fields"]]
    assert [m["name"] for m in d["measures"]] == [m["name"] for m in v["measures"]]
    assert [m["expr"] for m in d["measures"]] == [m["expr"] for m in v["measures"]]


def test_nested_joins_use_local_aliases_and_on_is_quoted():
    (rv,) = render(stub_plan(chain_model()))
    assert "\n  'on': " in rv.yaml or "\n- name: store\n" in rv.yaml
    assert "'on':" in rv.yaml and "\n  on:" not in rv.yaml
    d = doc_of(rv)
    (store,) = d["joins"]
    assert store["name"] == "store" and store["on"] == "source.`store_id` = `store`.`store_id`"
    (district,) = store["joins"]
    assert district["name"] == "district"
    assert district["on"] == "`store`.`district_id` = `district`.`district_id`"   # local parent alias
    assert district["source"] == "workspace.semabridge_dev.district"
    # field expressions keep the full path
    assert any(f["expr"] == "`store`.`district`.`district_name`" for f in d[FIELDS_KEY])


def test_labels_descriptions_warnings_and_formats():
    (rv,) = render(stub_plan(chain_model()))
    d = doc_of(rv)
    fields = {f["name"]: f for f in d[FIELDS_KEY]}
    assert fields["store_name"]["display_name"] == "Store Name"
    assert fields["store_name"]["comment"] == "Trading name of the store"
    assert fields["amount"]["format"] == {"type": "number", "decimal_places": {"type": "exact", "places": 2}}
    m = {x["name"]: x for x in d["measures"]}
    assert m["total_sales"]["comment"] == "All sales"
    assert m["total_sales"]["format"] == {"type": "currency", "currency_code": "USD",
                                          "decimal_places": {"type": "exact", "places": 2}}
    assert m["sales_ly"]["comment"].startswith("approximate:")


def test_one_to_many_join_is_declared():
    model = SMLModel(
        unique_name="m", label="B",
        datasets=[table("Store", "StoreID", "Region", "Capacity"), table("Sales", "SaleID", "StoreID", "Amount", fact=True)],
        relationships=[rel("Sales", "StoreID", "Store", "StoreID")],
        metrics=[SMLMetric(unique_name="Sales per Store", label="Sales per Store", dataset="Store",
                           expression="DIVIDE(SUM(Sales[Amount]), COUNTROWS(Store))",
                           sql_expression="SUM(SALES.`AMOUNT`::DOUBLE) / NULLIF(COUNT(*), 0)", sync_enabled=True)],
    )
    s = PlannerSettings(security_roles=0, output_catalog=OUT[0], output_schema=OUT[1], existing_objects=frozenset(),
                        capabilities=frozenset({Capability.ONE_TO_MANY_JOINS}))
    plan = stub_plan(model, settings=s)
    views = {v["name"]: v for v in plan["views"]}
    if not any(j["cardinality"] == "one_to_many" for v in views.values() for j in v["joins"]):
        pytest.skip("planner did not compose this measure (placement rules); covered by planner tests")
    rendered = render(plan)
    d = next(doc_of(r) for r in rendered if any(j.get("cardinality") == "one_to_many" for j in doc_of(r).get("joins", [])))
    (j,) = [x for x in d["joins"] if x.get("cardinality") == "one_to_many"]
    assert "joins" not in j


@pytest.mark.parametrize("fmt, spec, warns", [
    ("0", {"type": "number", "decimal_places": {"type": "exact", "places": 0}, "hide_group_separator": True}, False),
    ("#,##0", {"type": "number", "decimal_places": {"type": "exact", "places": 0}}, False),
    ("#,0.00", {"type": "number", "decimal_places": {"type": "exact", "places": 2}}, False),
    ("0.0%", {"type": "percentage", "decimal_places": {"type": "exact", "places": 1}, "hide_group_separator": True}, False),
    ("Percent", {"type": "percentage", "decimal_places": {"type": "exact", "places": 2}}, False),
    ("€#,##0.00", {"type": "currency", "currency_code": "EUR", "decimal_places": {"type": "exact", "places": 2}}, False),
    ("[$$-409]#,##0", {"type": "currency", "currency_code": "USD", "decimal_places": {"type": "exact", "places": 0}}, False),
    ("yyyy-mm-dd", {"type": "date", "date_format": "year_month_day"}, False),
    ("\\$#,0;(\\$#,0);\\$#,0", None, True),        # bare $ is ambiguous: never inferred
    ("$#,0.00", None, True),
    ("¥#,##0", None, True),
    ("Currency", None, True),
    ("mmm yyyy", None, True),
    ("General Number", None, False),
    (None, None, False),
])
def test_format_translation_is_conservative(fmt, spec, warns):
    got, warning = translate_format(fmt)
    assert got == spec
    assert bool(warning) is warns


def test_table_reference_quotes_only_parts_that_need_it():
    assert table_reference("workspace", "sb", "sales") == "workspace.sb.sales"
    assert table_reference("workspace", "my schema", "sa`les") == "workspace.`my schema`.`sa``les`"


def test_unicode_labels_and_awkward_names_round_trip():
    model = chain_model()
    model.datasets[1].columns[1].label = "Nom du magasin – été"
    (rv,) = render(stub_plan(model))
    assert "Nom du magasin – été" in rv.yaml                     # Unicode kept, not escaped
    assert doc_of(rv)  # parses


def test_output_is_deterministic():
    plan = stub_plan(chain_model())
    a, b = render(copy.deepcopy(plan)), render(copy.deepcopy(plan))
    assert [x.yaml for x in a] == [x.yaml for x in b]
    assert [x.body_sha256 for x in a] == [x.body_sha256 for x in b]


# --- fail closed ---------------------------------------------------------------------------

def _first_view(plan):
    return plan["views"][0]


@pytest.mark.parametrize("mutate, code", [
    (lambda p: p["meta"].__setitem__("status", "stale"), "plan_stale"),
    (lambda p: p.__setitem__("tables", []), "table_location_missing"),
    (lambda p: _first_view(p)["fields"][0].__setitem__("display_name", "a $$ b"), "dollar_quote_in_body"),
    (lambda p: _first_view(p)["measures"][0].__setitem__("expr", "SUM(`other`.`x`)"), "unknown_reference"),
    (lambda p: _first_view(p)["measures"][0].__setitem__("expr", "SUM(SALES.`x`)"), "unknown_reference"),
    (lambda p: _first_view(p)["fields"][0].__setitem__("name", "Bad Name"), "illegal_name"),
    (lambda p: _first_view(p)["fields"][1].__setitem__("name", _first_view(p)["fields"][0]["name"]), "duplicate_name"),
    (lambda p: _first_view(p)["fields"][0].__setitem__("name", "store"), "name_is_join_alias"),
    (lambda p: _first_view(p)["fields"][0].__setitem__("synonyms", [f"s{i}" for i in range(11)]), "synonyms_over_limit"),
    (lambda p: _first_view(p)["fields"][0].__setitem__("display_name", "x" * 256), "label_too_long"),
    (lambda p: _first_view(p)["joins"][0].__setitem__("on_local", ""), "plan_predates_writer"),
    (lambda p: _first_view(p)["joins"][0].__setitem__("on_local", "source.`a` = `elsewhere`.`a`"), "unknown_reference"),
])
def test_writer_refuses_instead_of_guessing(mutate, code):
    plan = stub_plan(chain_model())
    mutate(plan)
    with pytest.raises(WriterError) as exc:
        render(plan)
    assert exc.value.code == code


def test_no_output_location_is_refused():
    plan = stub_plan(chain_model())
    with pytest.raises(WriterError) as exc:
        render_plan(plan, catalog=None, schema="x", model_label="m")
    assert exc.value.code == "output_location_missing"


def test_probe_substitution_is_restricted():
    with pytest.raises(PermissionError):
        ProbeSources({"Sales": "SELECT 1"}, key=object())
    plan = stub_plan(chain_model())
    with pytest.raises(PermissionError):
        render(plan, probe={"Sales": "SELECT 1"})


def test_probe_substitution_puts_inline_queries_in_sources():
    from semabridge.target_backends.databricks import writer

    plan = stub_plan(chain_model())
    q = {"Sales": "SELECT * FROM VALUES (1, 1, 10.0) AS t(sale_id, store_id, amount)",
         "Store": "SELECT * FROM VALUES (1, 'A', 1, 5) AS t(store_id, store_name, district_id, capacity)",
         "District": "SELECT * FROM VALUES (1, 'D') AS t(district_id, district_name)"}
    (rv,) = render(plan, probe=ProbeSources(q, key=writer._PROBE_KEY))
    d = doc_of(rv)
    assert d["source"] == q["Sales"] and d["joins"][0]["source"] == q["Store"]


# --- proposed table locations and scoped blockers --------------------------------------------

def test_missing_tables_get_proposed_locations_that_avoid_existing_objects_and_views():
    plan = stub_plan(chain_model(), existing=frozenset({"sales", "store_2", "sales_metrics"}))
    tables = {t["dataset"]: t for t in plan["tables"]}
    assert tables["Sales"]["name"] == "sales_2"            # "sales" exists already
    assert tables["Store"]["name"] == "store"
    assert all(t["status"] == "proposed_not_created" and t["name_checked"] for t in tables.values())
    names = {t["name"] for t in tables.values()} | {v["name"] for v in plan["views"]}
    assert len(names) == len(tables) + len(plan["views"])  # no collisions with views either
    missing = [b for b in plan["blockers"] if b["code"] == "source_table_missing"]
    assert {b["table"] for b in missing} == {"Sales", "Store", "District"}
    assert all(b["scope"] == "table" and b["views"] == ["sales_metrics"] for b in missing)


def test_reserved_own_tables_keep_their_names():
    s = PlannerSettings(security_roles=0, output_catalog=OUT[0], output_schema=OUT[1],
                        existing_objects=frozenset({"sales"}), reserved_table_names={"Sales": "sales"})
    plan = stub_plan(chain_model(), settings=s)
    assert {t["dataset"]: t["name"] for t in plan["tables"]}["Sales"] == "sales"


def test_bound_tables_are_bindings_not_proposals():
    sales = UcTable("workspace", "sb_tests", "sales", "MANAGED",
                    {"SALEID": ("sale_id", "bigint"), "STOREID": ("store_id", "bigint"), "AMOUNT": ("amount", "double")})
    model = chain_model()
    tp = plan_tables(model, {("workspace", "sb_tests"): {"SALES": sales}}, source_schemas=[("workspace", "sb_tests")],
                     on_missing_source="stub")
    plan = plan_metric_views(model, tp, PlannerSettings(security_roles=0, output_catalog=OUT[0], output_schema=OUT[1],
                                                       existing_objects=frozenset())).as_dict()
    t = {x["dataset"]: x for x in plan["tables"]}
    assert t["Sales"]["status"] == "bound" and (t["Sales"]["catalog"], t["Sales"]["schema"]) == ("workspace", "sb_tests")
    assert t["Store"]["status"] == "proposed_not_created"
    assert not any(b.get("table") == "Sales" for b in plan["blockers"])


def test_no_connection_and_no_output_location_blockers():
    plan = plan_metric_views(chain_model(), None, PlannerSettings(security_roles=0)).as_dict()
    codes = {b["code"]: b for b in plan["blockers"]}
    assert codes["tables_not_checked"]["scope"] == "model"
    assert codes["output_location_missing"]["scope"] == "model"
    assert all(t["status"] == "unchecked" and not t["name_checked"] for t in plan["tables"])


# --- bundles -------------------------------------------------------------------------------

def test_bundle_is_atomic_current_moves_forward_and_only_current_is_deployable(tmp_path):
    plan = stub_plan(chain_model())
    rendered = render(plan)
    bundle.write_bundle(tmp_path, plan, rendered, plan_revision=100, catalog=OUT[0], schema=OUT[1])
    assert bundle.set_current(tmp_path, 100)
    m = bundle.deployable_bundle(tmp_path, 100)
    assert m["views"][0]["view"] == "sales_metrics" and m["proposed_tables_not_created"] == ["District", "Sales", "Store"]
    assert any(b["code"] == "source_table_missing" for b in m["blockers"])
    # an older revision can never become current again; a newer one does
    bundle.write_bundle(tmp_path, plan, rendered, plan_revision=90, catalog=OUT[0], schema=OUT[1])
    assert not bundle.set_current(tmp_path, 90)
    bundle.write_bundle(tmp_path, plan, rendered, plan_revision=200, catalog=OUT[0], schema=OUT[1])
    assert bundle.set_current(tmp_path, 200)
    with pytest.raises(bundle.BundleError):
        bundle.deployable_bundle(tmp_path, 100)            # obsolete after re-plan
    # a changed file is caught
    (tmp_path / "views" / "200" / "sales_metrics.yaml").write_bytes(b"changed")
    with pytest.raises(bundle.BundleError):
        bundle.deployable_bundle(tmp_path, 200)


def test_failed_bundle_never_becomes_a_revision(tmp_path, monkeypatch):
    plan = stub_plan(chain_model())
    rendered = render(plan) * 2
    rendered[1] = copy.copy(rendered[1])
    rendered[1].view = "second"
    calls = {"n": 0}
    real = Path.write_bytes

    def flaky(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "write_bytes", flaky)
    with pytest.raises(OSError):
        bundle.write_bundle(tmp_path, plan, rendered, plan_revision=300, catalog=OUT[0], schema=OUT[1])
    monkeypatch.setattr(Path, "write_bytes", real)
    views = tmp_path / "views"
    assert not (views / "300").exists()
    assert [p.name for p in views.iterdir()] == []          # temp folder removed too
    assert not bundle.set_current(tmp_path, 300)


def test_probe_bundles_are_never_deployable(tmp_path):
    plan = stub_plan(chain_model())
    rendered = render(plan)
    bundle.write_bundle(tmp_path, plan, rendered, plan_revision=400, catalog=OUT[0], schema=OUT[1], probe=True)
    assert not (tmp_path / "views" / "400").exists()
    assert (tmp_path / "probes" / "400" / "manifest.json").exists()
    assert not bundle.set_current(tmp_path, 400)
    with pytest.raises(bundle.BundleError):
        bundle.deployable_bundle(tmp_path, 400)


def test_attach_definitions_marks_unavailable_with_a_blocker(tmp_path):
    plan = stub_plan(chain_model())
    plan["tables"] = []                                     # nothing to read from
    summary = bundle.attach_definitions(plan, tmp_path, catalog=OUT[0], schema=OUT[1], model_label="m")
    assert summary["status"] == "unavailable" and summary["error_code"] == "table_location_missing"
    assert plan["definitions"] == [] and plan["summary"]["publication_blocked"] is True
    assert any(b["code"] == "view_definitions_unavailable" and b["scope"] == "model" for b in plan["blockers"])
    ok = stub_plan(chain_model())
    ok["meta"]["revision"] = 101
    assert bundle.attach_definitions(ok, tmp_path, catalog=OUT[0], schema=OUT[1], model_label="m")["status"] == "written"
    assert ok["definitions"][0]["create_sql"].startswith("CREATE VIEW")
    manifest = json.loads((tmp_path / "views" / "101" / "manifest.json").read_text())
    assert manifest["plan_revision"] == 101 and manifest["probe"] is False
