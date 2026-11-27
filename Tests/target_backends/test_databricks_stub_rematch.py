"""Re-planning after a deploy: SemaBridge's own empty stub tables stay stubs, and
table matching uses the Databricks naming rule (the rule stubs are created with).

Bug: stub columns are created as snake_case ('CampaignSeq' -> campaign_seq) but
table matching scored them with the shared rule ('CAMPAIGNSEQ'), so on the next
plan some of our stubs were rejected (75%) and others bound as real data (80%).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from semabridge.target_backends.databricks.tables import UcTable, plan_tables


def col(name, dtype="string", calculated=False, expr=None):
    return SimpleNamespace(unique_name=name, data_type=SimpleNamespace(value=dtype), is_calculated=calculated,
                           source_expression=expr)


def dataset(name, *cols):
    return SimpleNamespace(unique_name=name, columns=list(cols))


def model(datasets, metrics=()):
    return SimpleNamespace(unique_name="AI Sample", label="AI Sample", datasets=list(datasets),
                           metrics=[SimpleNamespace(unique_name=f"m{i}", sql_expression=s) for i, s in enumerate(metrics)])


def meta(*tables):
    out: Dict[Any, Dict[str, UcTable]] = {}
    for t in tables:
        out.setdefault((t.catalog, t.schema), {})[t.name.upper()] = t
    return out

DEST = ("workspace", "semabridge_ai")
SRC = [DEST]

CAMPAIGNS = dataset("Campaigns", col("CampaignSeq", "integer"), col("Type"), col("Campaign Name"),
                    col("Factor", "integer"))
PRODUCTS = dataset("Products", col("Product category"), col("Product"), col("MinOppValue", "decimal"),
                   col("MaxOppValue", "decimal"), col("ProductSeq", "integer"))
ACCOUNTS = dataset("Accounts", col("Account Name"), col("Street"), col("City"), col("State or Province"),
                   col("Postal Code"), col("Country"), col("Latitude", "float"), col("Longitude", "float"),
                   col("Phone"), col("Annual Revenue", "integer"), col("Number of Employees", "integer"),
                   col("Account Owner"), col("AccountID"), col("Territory"), col("Region"),
                   col("AccountSeq", "integer"), col("IndustrySeq", "integer"), col("AccountOwnerSeq", "integer"),
                   col("Industry"), col("Account Number"))
AI_MODEL = [CAMPAIGNS, PRODUCTS, ACCOUNTS]
STUB_NAMES = {"Campaigns": "campaigns", "Products": "products", "Accounts": "accounts"}


def table_from(name: str, columns: List[str]) -> UcTable:
    t = UcTable(DEST[0], DEST[1], name, "MANAGED")
    for c in columns:
        t.columns[c.upper()] = (c, "string")
    return t


def _shape(plan) -> List[Any]:
    return [(b.dataset, b.action, list(b.stub_columns), dict(b.column_map)) for b in plan.bindings]


def _stubs_as_tables(first_plan) -> Dict[Any, Dict[str, UcTable]]:
    """What the first deploy created: one empty table per stub, with exactly its stub columns."""
    return meta(*[table_from(STUB_NAMES[b.dataset], [n for n, _t in b.stub_columns])
                  for b in first_plan.bindings if b.action == "stub"])


# --- round trip (the missing regression) -------------------------------------------------------


def test_replan_after_stubs_exist_is_identical_to_the_first_plan() -> None:
    first = plan_tables(model(AI_MODEL), {DEST: {}}, source_schemas=SRC)
    assert [b.action for b in first.bindings] == ["stub", "stub", "stub"]
    existing = _stubs_as_tables(first)
    own = {f"{DEST[0]}.{DEST[1]}.{n}": "" for n in STUB_NAMES.values()}

    again = plan_tables(model(AI_MODEL), existing, source_schemas=SRC, semabridge_tables=own)

    assert _shape(again) == _shape(first)
    assert not any("exists but only" in w for w in again.warnings)
    assert again.warnings == first.warnings


def test_without_recognition_our_stubs_would_now_bind_by_name() -> None:
    """The naming fix alone makes stub columns match 100%; recognising our own
    tables is what keeps them stubs (never bound as real source data)."""
    first = plan_tables(model(AI_MODEL), {DEST: {}}, source_schemas=SRC)
    unrecognised = plan_tables(model(AI_MODEL), _stubs_as_tables(first), source_schemas=SRC)
    assert [b.action for b in unrecognised.bindings] == ["bind", "bind", "bind"]
    assert not any("exists but only" in w for w in unrecognised.warnings)


def test_another_projects_or_unverified_semabridge_table_is_not_used_and_is_reported() -> None:
    table = table_from("campaigns", ["campaign_seq", "type", "campaign_name", "factor"])
    note = "workspace.semabridge_ai.campaigns is an empty table created by another SemaBridge project; x"
    plan = plan_tables(model([CAMPAIGNS]), meta(table), source_schemas=SRC,
                       semabridge_tables={"`workspace`.`semabridge_ai`.`campaigns`": note})
    assert plan.bindings[0].action == "stub"
    assert note in plan.warnings


def test_explicit_mapping_to_a_semabridge_table_is_not_overridden() -> None:
    table = table_from("campaigns", ["campaign_seq", "type", "campaign_name", "factor"])
    plan = plan_tables(model([CAMPAIGNS]), meta(table), source_schemas=SRC,
                       table_mapping={"Campaigns": "workspace.semabridge_ai.campaigns"},
                       semabridge_tables={"workspace.semabridge_ai.campaigns": ""})
    assert plan.bindings[0].action == "bind" and plan.bindings[0].matched_by == "mapping"


# --- real snake_case user tables --------------------------------------------------------------


def test_real_snake_case_tables_bind_with_real_column_names() -> None:
    campaigns = table_from("campaigns", ["campaign_seq", "type", "campaign_name", "factor"])
    products = table_from("products", ["product_category", "product", "min_opp_value", "max_opp_value", "product_seq"])
    plan = plan_tables(model([CAMPAIGNS, PRODUCTS]), meta(campaigns, products), source_schemas=SRC)
    c, p = plan.bindings
    assert (c.action, p.action) == ("bind", "bind")
    assert c.column_map["CAMPAIGNSEQ"] == "campaign_seq"
    assert p.column_map["MINOPPVALUE"] == "min_opp_value" and p.column_map["PRODUCTSEQ"] == "product_seq"
    assert not c.missing_columns and not p.missing_columns and not c.ambiguous_columns
    assert not any("exists but only" in w for w in plan.warnings)


def test_rejected_table_warning_uses_the_same_rule() -> None:
    """A genuinely different table is still rejected, and its score now counts
    snake_case matches (2 of 4 here), not only the shared rule's."""
    table = table_from("campaigns", ["campaign_seq", "type", "budget", "owner"])
    plan = plan_tables(model([CAMPAIGNS]), meta(table), source_schemas=SRC)
    assert plan.bindings[0].action == "stub"
    assert any("only 50% of its columns match" in w for w in plan.warnings)


# --- ambiguity is never resolved by the naming rule -------------------------------------------------


def test_two_model_columns_one_real_column_stay_ambiguous() -> None:
    ds = dataset("Campaigns", col("CampaignSeq", "integer"), col("Campaign Seq", "integer"), col("Type"),
                 col("Campaign Name"), col("Factor", "integer"))
    table = table_from("campaigns", ["campaign_seq", "type", "campaign_name", "factor"])
    b = plan_tables(model([ds]), meta(table), source_schemas=SRC).bindings[0]
    assert b.action == "bind"   # 4 of 5 by the shared rule alone, as before
    assert "CampaignSeq" in b.ambiguous_columns or "CampaignSeq" in b.missing_columns
    assert "CAMPAIGNSEQ" not in b.column_map


def test_two_real_columns_one_model_column_add_no_match() -> None:
    """'campaign_seq' and 'campaign seq' both normalise to campaign_seq: the naming
    rule adds nothing for CampaignSeq, so the score is what the shared rule alone
    gives (3 of 4) -- rejected, never a guess between the two columns."""
    table = table_from("campaigns", ["campaign_seq", "campaign seq", "type", "campaign_name", "factor"])
    plan = plan_tables(model([CAMPAIGNS]), meta(table), source_schemas=SRC)
    assert plan.bindings[0].action == "stub"
    assert any("only 75% of its columns match" in w for w in plan.warnings)


def test_case_insensitive_exact_name_still_wins_over_the_naming_rule() -> None:
    table = table_from("campaigns", ["campaign_seq", "campaignseq", "type", "campaign_name", "factor"])
    b = plan_tables(model([CAMPAIGNS]), meta(table), source_schemas=SRC).bindings[0]
    assert b.action == "bind" and b.column_map["CAMPAIGNSEQ"] == "campaignseq"
    assert b.column_match["CampaignSeq"] == "case_insensitive"


def test_two_candidate_tables_still_stop_the_run() -> None:
    from semabridge.target_backends.databricks.tables import TableMatchingError

    a = table_from("campaigns_a", ["campaign_seq", "type", "campaign_name", "factor"])
    b = table_from("campaigns_b", ["campaign_seq", "type", "campaign_name", "factor"])
    try:
        plan = plan_tables(model([CAMPAIGNS]), meta(a, b), source_schemas=SRC)
    except TableMatchingError:
        return
    # equal-score ties with partial names may count as missing (shared resolver rule): never a guess
    assert plan.bindings[0].action == "stub"


# --- recognising SemaBridge's tables (step 6b helper) -----------------------------------------------


class _Client:
    def __init__(self, ids: Dict[str, str], metastore: str = "ms-1"):
        self.ids, self.metastore, self.gets, self.sql = ids, metastore, [], []

    def get(self, path):
        from urllib.parse import unquote

        name = unquote(path.rsplit("/", 1)[-1])
        self.gets.append(name)
        return {"table_id": self.ids.get(name)}

    def execute(self, sql):
        self.sql.append(sql)
        return SimpleNamespace(rows=[{"m": self.metastore}])


def _records(monkeypatch, records=None, error=None):
    from semabridge.target_backends.databricks.deploy import store

    def fake(fqns):
        if error:
            raise error
        return {k: v for k, v in (records or {}).items() if k in set(fqns)}

    monkeypatch.setattr(store, "recorded_stub_tables", fake)


def _meta_with(*names):
    return meta(*[table_from(n, ["x"]) for n in names])


def _rec(project="proj", uc="id-1", verified=True, metastore="ms-1"):
    return {"project_id": project, "uc_object_id": uc, "verified": verified, "metastore_id": metastore}


def test_semabridge_tables_recognition_rules(monkeypatch) -> None:
    from semabridge.core.engine.targets.databricks import _semabridge_tables

    base = "workspace.semabridge_ai."
    _records(monkeypatch, {
        base + "own": _rec(),
        base + "other": _rec(project="someone-else", uc="id-2"),
        base + "unverified": _rec(uc="id-3", verified=False),
        base + "replaced": _rec(uc="old-id"),                 # dropped and recreated by someone else
        base + "elsewhere": _rec(uc="id-5", metastore="ms-9"),
    })
    client = _Client({base + "own": "id-1", base + "other": "id-2", base + "unverified": "id-3",
                      base + "replaced": "new-id", base + "elsewhere": "id-5", base + "plain": "id-6"})
    out = _semabridge_tables(client, _meta_with("own", "other", "unverified", "replaced", "elsewhere", "plain"), "proj")
    assert out[base + "own"] == ""
    assert "another SemaBridge project" in out[base + "other"]
    assert "not verified" in out[base + "unverified"]
    assert base + "replaced" not in out and base + "elsewhere" not in out and base + "plain" not in out
    # UC ids are read only for names SemaBridge has records for; reads only
    assert sorted(client.gets) == sorted(base + n for n in ("own", "other", "unverified", "replaced", "elsewhere"))
    assert all(s.lstrip().upper().startswith("SELECT") for s in client.sql)


def test_semabridge_tables_recognises_nothing_when_records_are_unreadable(monkeypatch) -> None:
    from semabridge.core.engine.targets.databricks import _semabridge_tables

    _records(monkeypatch, error=RuntimeError("db down"))
    client = _Client({})
    assert _semabridge_tables(client, _meta_with("campaigns"), "proj") == {}
    assert not client.gets


def test_semabridge_tables_without_records_makes_no_databricks_calls(monkeypatch) -> None:
    from semabridge.core.engine.targets.databricks import _semabridge_tables

    _records(monkeypatch, {})
    client = _Client({})
    assert _semabridge_tables(client, _meta_with("campaigns"), "proj") == {}
    assert not client.gets and not client.sql


# --- the store lookup -----------------------------------------------------------------------------


def test_recorded_stub_tables_returns_the_latest_stub_record_per_table() -> None:
    from semabridge.target_backends.databricks.deploy import store

    pid = f"p-{uuid.uuid4().hex[:8]}"
    fqn = f"workspace.sb_{uuid.uuid4().hex[:8]}.campaigns"
    for run, outcome, uc in (("r1", "created", "id-1"), ("r2", "applied_unverified", "id-1")):
        run_id = f"{run}-{uuid.uuid4().hex[:8]}"
        store.start_run(run_id=run_id, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                        catalog="workspace", schema="x", stub_form="empty_table", metastore_id="ms-1")
        store.record_object(run_id=run_id, project_id=pid, model_key="", kind="stub_table", fqn=fqn,
                            outcome=outcome, uc_object_id=uc)
    view_run = f"v-{uuid.uuid4().hex[:8]}"
    store.start_run(run_id=view_run, project_id=pid, model_key="", plan_revision=1, writer_revision="1",
                    catalog="workspace", schema="x", stub_form="empty_table")
    store.record_object(run_id=view_run, project_id=pid, model_key="", kind="view", fqn=fqn + "_metrics",
                        outcome="created", uc_object_id="id-9")

    out = store.recorded_stub_tables([fqn.upper(), fqn + "_metrics", "workspace.none.none"])
    assert list(out) == [fqn]
    rec = out[fqn]
    assert (rec["project_id"], rec["uc_object_id"], rec["outcome"], rec["verified"], rec["metastore_id"]) == \
        (pid, "id-1", "applied_unverified", False, "ms-1")
    assert store.recorded_stub_tables([]) == {}
