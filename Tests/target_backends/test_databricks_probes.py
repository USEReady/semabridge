"""Phase 3.5 capability probes, offline.

* The fixtures' hand-computed expectations are re-derived here independently,
  from the rows, with plain-Python DAX semantics (BLANK handling, DIVIDE,
  distinct counts) -- the live comparison is only as good as these numbers.
* Every fixture builds through the production converter -> planner -> writer.
* The executor runs against a fake Databricks that enforces the real guard:
  success, wrong numbers, refusals, ambiguous creates (owned / not owned),
  name clashes, preflight failure, cleanup failure.
* Evidence validity and effective capabilities (proof AND admin allow-list).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from semabridge.target_backends.databricks.guard import (
    DatabricksGuardError,
    DatabricksSqlGuard,
    WRITE,
    WritePlan,
    validate_databricks_sql,
)
from semabridge.target_backends.databricks.probes import compare, evidence, executor, fixtures as F, pipeline

D = Decimal


# --- independent reference semantics -----------------------------------------------------------

def rows_of(table, **where):
    cols = [c.name for c in table.columns]
    out = [dict(zip(cols, r)) for r in table.rows]
    return [r for r in out if all(r[k] == v for k, v in where.items())]


def s_sum(vals):
    v = [x for x in vals if x is not None]
    return sum(v, D(0)) if v else None


def s_avg(vals):
    v = [x for x in vals if x is not None]
    return sum(v, D(0)) / len(v) if v else None


def blank_add(a, b):
    return None if a is None and b is None else (a or 0) + (b or 0)


def blank_sub(a, b):
    return None if a is None and b is None else (a or 0) - (b or 0)


def mul(a, b):
    return None if a is None or b is None else a * b


def divide(a, b, alt=None):
    if b is None or b == 0:
        return alt
    return None if a is None else D(a) / D(b)


def store_ids(region=None, name=None):
    return [r["StoreID"] for r in rows_of(F._store()) if (region is None or r["Region"] == region)
            and (name is None or r["StoreName"] == name)]


def sales_for(stores=None, customer=None):
    return [r for r in rows_of(F.SALES) if (stores is None or r["StoreID"] in stores)
            and (customer is None or r["CustomerID"] == customer)]


def eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(D(a) - D(b)) < D("1e-20")


def test_p0_expectations():
    by = {q.qid: q.expected for q in F.P0.queries}
    assert eq(by["p0_total"][()]["Total Sales"], s_sum(r["Amount"] for r in sales_for()))
    for region in ("East", "West"):
        assert eq(by["p0_region"][(region,)]["Total Sales"], s_sum(r["Amount"] for r in sales_for(store_ids(region))))
    north = [r["StoreID"] for r in rows_of(F._store()) if r["DistrictID"] == 1]
    assert eq(by["p0_district"][("North",)]["Total Sales"], s_sum(r["Amount"] for r in sales_for(north)))
    assert eq(by["p0_filtered"][()]["Total Sales"], s_sum(r["Amount"] for r in sales_for(store_ids("West"))))


def test_p6_expectations():
    def ops(rows):
        a, b = s_sum(r["Amount"] for r in rows), s_sum(r["Budget"] for r in rows)
        return {"Op Add": blank_add(a, b), "Op Sub": blank_sub(a, b), "Op Mul": mul(a, b),
                "Op Div": divide(a, b), "Op Div Alt": divide(a, b, 0)}
    q = {x.qid: x.expected for x in F.P6.queries}
    for (cust,), want in q["p6_by_customer"].items():
        got = ops(sales_for(customer=cust))
        assert all(eq(want[k], got[k]) for k in want), (cust, want, got)
    got = ops(sales_for())
    assert all(eq(v, got[k]) for k, v in q["p6_total"][()].items())


def _agg(family, rows):
    amt = [r["Amount"] for r in rows]
    nn = [x for x in amt if x is not None]
    return {
        "SUM": s_sum(amt), "AVERAGE": s_avg(amt), "MIN": min(nn) if nn else None, "MAX": max(nn) if nn else None,
        "COUNT": len(nn), "COUNTA": sum(1 for r in rows if r["CustomerID"] is not None),
        "COUNTBLANK": sum(1 for r in rows if r["Budget"] is None),
        "DISTINCTCOUNT": len({r["CustomerID"] for r in rows}), "DISTINCTCOUNTNOBLANK": len({r["CustomerID"] for r in rows}),
        "SUMX": s_sum(x * 2 if x is not None else None for x in amt), "AVERAGEX": s_avg(amt),
        "MINX": min(nn) if nn else None, "MAXX": max(nn) if nn else None, "COUNTX": len(nn),
        "COUNTAX": sum(1 for r in rows if r["CustomerID"] is not None), "COUNTROWS": len(rows),
    }[family]


def test_p7_expectations_and_family_coverage():
    from semabridge.target_backends.databricks.planner.semantics import COLUMN_AGGREGATES, ITERATORS, ROW_COUNTERS

    assert set(F.AGGREGATE_FAMILIES) == COLUMN_AGGREGATES | ITERATORS | ROW_COUNTERS   # drift caught
    q = {x.qid: x.expected for x in F.P7.queries}
    for fam, name in F._FAMILY_NAMES.items():
        assert eq(q["p7_total"][()][name], _agg(fam, sales_for())), fam
        for region in ("East", "West"):
            assert eq(q["p7_region"][(region,)][name], _agg(fam, sales_for(store_ids(region)))), (fam, region)


def test_one_to_many_expectations():
    def p1(stores):
        rows = sales_for(stores)
        cap = s_sum(D(r["Capacity"]) for r in rows_of(F._store()) if r["StoreID"] in stores)
        sales = s_sum(r["Amount"] for r in rows)
        return {"Sales per Store": divide(sales, len(stores)), "Sales minus Capacity": blank_sub(sales, cap),
                "Sale Rows per Capacity": divide(len(rows) or None, cap)}
    q = {x.qid: x.expected for x in F.P1.queries}
    for (name,), want in q["p1_by_store"].items():
        got = p1(store_ids(name=name))
        assert all(eq(want[k], got[k]) for k in want), (name, got)
    for (region,), want in q["p1_by_region"].items():
        got = p1(store_ids(region))
        assert all(eq(want[k], got[k]) for k in want), (region, got)
    got = p1(store_ids())
    assert all(eq(v, got[k]) for k, v in q["p1_total"][()].items())
    assert all(eq(v, p1(store_ids("East"))[k]) for k, v in q["p1_root_filter"][()].items())


def test_one_to_many_b_context_expectations():
    def pb(stores):
        rows = sales_for(stores)
        cap = s_sum(D(r["Capacity"]) for r in rows_of(F._store()) if r["StoreID"] in stores)
        amounts = [r["Amount"] for r in rows if r["Amount"] is not None]
        budget = s_sum(r["Budget"] for r in rows)
        return {"B Avg plus Capacity": blank_add(s_avg(r["Amount"] for r in rows), cap),
                "B Customers per Store": divide(len({r["CustomerID"] for r in rows}), len(stores)),
                "B Max minus Capacity": blank_sub(max(amounts) if amounts else None, cap),
                "B Budget times Stores": mul(budget, len(stores)),
                "B Budget per Capacity": divide(budget, cap, 0)}
    q = {x.qid: x.expected for x in F.PB.queries}
    for (region,), want in q["pb_by_region"].items():
        got = pb(store_ids(region))
        assert all(eq(want[k], got[k]) for k in want), (region, got)
    got = pb(store_ids())
    assert all(eq(v, got[k]) for k, v in q["pb_total"][()].items()), got


def test_sibling_fact_expectations():
    def p5(months):
        a = s_sum(r["Amount"] for r in rows_of(F.ACTUAL) if r["MonthID"] in months)
        b = s_sum(r["Amount"] for r in rows_of(F.BUDGET) if r["MonthID"] in months)
        return {"Variance": blank_sub(a, b), "Combined": blank_add(a, b)}
    ids = {r["MonthName"]: r["MonthID"] for r in rows_of(F.MONTH)}
    q = {x.qid: x.expected for x in F.P5.queries}
    for (m,), want in q["p5_by_month"].items():
        assert all(eq(want[k], p5([ids[m]])[k]) for k in want), m
    assert all(eq(v, p5(list(ids.values()))[k]) for k, v in q["p5_total"][()].items())


# --- every fixture through the production path -------------------------------------------------

@pytest.mark.parametrize("fx", F.ALL, ids=lambda f: f.fid)
def test_fixture_builds_through_production_path(fx):
    b = pipeline.build(fx, catalog="workspace", schema="sb_tests", token="t0")
    assert b.plan["meta"]["probe"] is True
    assert all(r.view.startswith("semabridge_probe_t0_") for r in b.rendered)
    for r in b.rendered:
        assert "SELECT * FROM VALUES" in r.yaml and "probe_catalog" not in r.yaml   # inline data, no table names
    for q in fx.queries:
        sql, dims, ms = pipeline.query_sql(b, q, "workspace", "sb_tests")
        assert sql.startswith("SELECT ") and "MEASURE(" in sql
    if fx.capabilities:
        joins = [j for v in b.plan["views"] for j in v["joins"]]
        if F.ONE_TO_MANY in fx.capabilities:
            assert any(j["cardinality"] == "one_to_many" for j in joins)


def test_p7_declined_families_are_recorded_not_tested():
    b = pipeline.build(F.P7, catalog="w", schema="s", token="t0")
    assert set(b.skipped_measures) <= set(F.P7.optional)
    assert all("not placed by SemaBridge" in why or "converter" in why for why in b.skipped_measures.values())


# --- comparator -----------------------------------------------------------------------------------

def test_comparator_rules():
    assert compare.values_match(None, None) == (True, None)
    assert compare.values_match(None, 0)[0] is False and compare.values_match(0, None)[0] is False
    assert compare.values_match(D("130"), 130.0) == (True, None)
    assert compare.values_match(D(340) / 3, 113.33333333333333)[0]
    assert compare.values_match(D(340) / 3, 113.3)[0] is False
    assert compare.values_match(D("1.5"), D("1.50")) == (True, None)
    assert compare.values_match(D(5) / 3, D("1.666667"))[1] == "DECIMAL rounded to 6 places"
    assert compare.values_match(D(5) / 3, D("1.666666"))[0] is False
    assert compare.values_match(3, 3)[0] and compare.values_match("East", "East")[0]
    r = compare.compare({("A",): {"m": D(1)}, ("B",): {"m": D(2)}},
                        [{"g": "A", "c": 1.0}, {"g": "A", "c": 1.0}, {"g": "C", "c": 3}], ["g"], [("m", "c")])
    assert not r["ok"] and r["missing"] == ["('B',)"] and r["extra"] == ["('C',)"] and r["duplicates"] == ["('A',)"]


# --- guard: the narrow probe category ---------------------------------------------------------

def test_guard_probe_category():
    fqn = "workspace.sb_tests.semabridge_probe_ab12_p0_sales_metrics"
    plain = "workspace.sb_tests.semabridge_probe_ab12_data_store"
    plan = WritePlan.for_probes([fqn], [plain])
    ok = validate_databricks_sql(f"CREATE VIEW {fqn} WITH METRICS LANGUAGE YAML AS $$ version: 1.1 $$", WRITE, plan)
    assert ok.kind == "CREATE_PROBE_VIEW"
    assert validate_databricks_sql(f"DROP VIEW IF EXISTS {fqn}", WRITE, plan).kind == "DROP_PROBE_VIEW"
    assert validate_databricks_sql(f"CREATE VIEW {plain} AS SELECT * FROM VALUES (1) AS t(a)", WRITE, plan).kind \
        == "CREATE_PROBE_PLAIN_VIEW"
    refused = [
        f"CREATE OR REPLACE VIEW {fqn} WITH METRICS LANGUAGE YAML AS $$ x $$",          # never replace
        f"CREATE VIEW {fqn} AS SELECT 1",                                               # metric view required
        "CREATE VIEW workspace.sb_tests.other WITH METRICS LANGUAGE YAML AS $$ x $$",    # not this run's object
        "DROP VIEW workspace.sb_tests.sales",                                           # not ours
        "CREATE TABLE workspace.sb_tests.semabridge_probe_ab12_t (a INT)",              # table DDL never
        "DROP TABLE workspace.sb_tests.semabridge_probe_ab12_p0_sales_metrics",
        f"CREATE VIEW {plain} AS SELECT * FROM workspace.sb_tests.sales; DROP TABLE x",
    ]
    for sql in refused:
        with pytest.raises(DatabricksGuardError):
            validate_databricks_sql(sql, WRITE, plan)
    with pytest.raises(ValueError):
        WritePlan.for_probes(["workspace.sb_tests.sales"])


# --- executor against a fake Databricks (the real guard is enforced) ----------------------------

class FakeDatabricks:
    """Answers the probe run's statements; every statement passes the real guard first."""

    def __init__(self, *, wrong=None, refuse=None, ambiguous=None, owned=True, existing=(), preflight_error=None,
                 drop_error=None):
        self.run = None
        self.wrong, self.refuse, self.ambiguous, self.owned = wrong, refuse, ambiguous, owned
        self.existing, self.preflight_error, self.drop_error = {e.lower() for e in existing}, preflight_error, drop_error
        self.created, self.dropped, self.statements = set(), [], []
        self.answers = None

    def factory(self, guard=None, warehouse_id=None):
        fake = self

        class Client:
            def __init__(self):
                self.guard = guard or DatabricksSqlGuard()
                self.warehouse_id = warehouse_id or "wh1"

            def execute(self, sql, **_kw):
                return fake.execute(self.guard, sql)

        return Client()

    def _answers(self):
        if self.answers is None:
            self.answers = {}
            for fx in F.ALL:
                b = pipeline.build(fx, catalog="workspace", schema="sb_tests", token=self.run.token)
                for q in fx.queries:
                    sql, dims, ms = pipeline.query_sql(b, q, "workspace", "sb_tests")
                    rows = []
                    for key, vals in (q.expected or {}).items():
                        row = dict(zip(dims, key))
                        for i, m in enumerate(ms):
                            v = vals.get(m)
                            row[f"m_{i}"] = float(v) if isinstance(v, Decimal) else v
                        rows.append(row)
                    if not q.expected:
                        rows = [{"region": "East", "m_0": 1.0}]
                    if self.wrong and q.qid == self.wrong:
                        rows[0][f"m_0"] = (rows[0]["m_0"] or 0) + 1
                    self.answers[sql] = rows
        return self.answers

    def execute(self, guard, sql):
        from semabridge.target_backends.databricks.client import (
            DatabricksSqlError, DatabricksStatementError, DatabricksTimeoutError, StatementResult)

        decision = guard.check(sql)
        self.statements.append(sql)
        if sql.startswith("SELECT current_version()"):
            if self.preflight_error:
                raise DatabricksSqlError(self.preflight_error)
            return StatementResult(statement_id="s", kind="SELECT", rows=[{"v": {"dbsql_version": "2026.46"}}])
        if "information_schema.tables" in sql:
            names = set(re.findall(r"'(semabridge_probe_[^']+)'", sql))
            return StatementResult(statement_id="s", kind="SELECT",
                                   rows=[{"table_name": n} for n in names if n in self.existing])
        if decision.kind == "CREATE_PROBE_VIEW":
            name = decision.object_name
            if self.refuse and self.refuse in name:
                raise DatabricksStatementError("[METRIC_VIEW_INVALID] one_to_many is not supported", error_code="X")
            if self.ambiguous and self.ambiguous in name:
                if self.owned:
                    self.created.add(name)
                raise DatabricksTimeoutError("timed out waiting for the statement")
            self.created.add(name)
            return StatementResult(statement_id="s", kind="CREATE")
        if sql.startswith("DESCRIBE TABLE EXTENDED"):
            name = sql.split()[-1].replace("`", "").lower()
            text = f"comment: Created by SemaBridge from model probe {self.run.token}" if name in self.created else ""
            return StatementResult(statement_id="s", kind="DESCRIBE", rows=[{"col_name": "View Text", "data_type": text}])
        if decision.kind == "DROP_PROBE_VIEW":
            if self.drop_error:
                raise DatabricksSqlError(self.drop_error)
            self.dropped.append(decision.object_name)
            self.created.discard(decision.object_name)
            return StatementResult(statement_id="s", kind="DROP")
        if sql.startswith("SELECT "):
            return StatementResult(statement_id="s", kind="SELECT", rows=self._answers()[sql])
        raise AssertionError(f"unexpected statement {sql[:80]}")


def _run(tmp_path, fake, monkeypatch, record=False):
    info = SimpleNamespace(host=f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com", warehouse_id="wh1")
    run = executor.ProbeRun(info, probe_catalog="workspace", probe_schema="sb_tests", client_factory=fake.factory,
                            manifest_root=tmp_path, record_evidence=record)
    fake.run = run
    return run, run.run()


def test_successful_run_proves_every_gate_and_cleans_up(tmp_path, monkeypatch):
    fake = FakeDatabricks()
    run, report = _run(tmp_path, fake, monkeypatch)
    assert {g: v["outcome"] for g, v in report["gates"].items()} == {
        "baseline": "proven", "one_to_many_joins": "proven", "mixed_root_views": "proven", "constant_coalesce": "proven"}
    assert report["dbsql_version"] == "2026.46"
    assert not fake.created and len(report["cleanup"]["dropped"]) == len(fake.dropped) > 0
    assert report["cleanup"]["left_untouched"] == [] and report["cleanup"]["errors"] == []
    manifest = json.loads(run.manifest_path.read_text())
    assert all(o["state"] == "dropped" for o in manifest["objects"])
    assert all(n.split(".")[-1].startswith(f"semabridge_probe_{run.token}_") for n in fake.dropped)
    # nothing but SELECT / SHOW / probe CREATE / probe DROP was sent
    assert all(s.split()[0] in ("SELECT", "DESCRIBE", "CREATE", "DROP") for s in fake.statements)


def test_a_wrong_number_fails_only_its_gate(tmp_path, monkeypatch):
    fake = FakeDatabricks(wrong="p1_total")
    _, report = _run(tmp_path, fake, monkeypatch)
    assert report["gates"]["one_to_many_joins"]["outcome"] == "wrong_result"
    assert report["gates"]["mixed_root_views"]["outcome"] == "inconclusive"    # depends on one-to-many
    assert report["gates"]["baseline"]["outcome"] == "proven"
    q = report["fixtures"]["p1"]["queries"]["p1_total"]
    assert q["mismatches"] and q["outcome"] == "wrong_result"


def test_databricks_refusal_is_unsupported_and_nothing_is_dropped_for_it(tmp_path, monkeypatch):
    fake = FakeDatabricks(refuse="_p5_")
    _, report = _run(tmp_path, fake, monkeypatch)
    assert report["fixtures"]["p5"]["outcome"] == "unsupported"
    assert report["gates"]["one_to_many_joins"]["outcome"] == "unsupported"
    assert not any("_p5_" in d for d in fake.dropped)


def test_ambiguous_create_owned_is_cleaned_not_owned_is_left_untouched(tmp_path, monkeypatch):
    fake = FakeDatabricks(ambiguous="_p3_", owned=True)
    _, report = _run(tmp_path, fake, monkeypatch)
    assert any("_p3_" in d for d in fake.dropped) and report["fixtures"]["p3"]["outcome"] == "inconclusive"
    fake2 = FakeDatabricks(ambiguous="_p3_", owned=False)
    _, report2 = _run(tmp_path, fake2, monkeypatch)
    assert not any("_p3_" in d for d in fake2.dropped)
    assert any("_p3_" in x["fqn"] for x in report2["cleanup"]["left_untouched"])


def test_existing_object_name_is_never_touched(tmp_path, monkeypatch):
    fake = FakeDatabricks()
    fake.run = SimpleNamespace(token="x")
    info = SimpleNamespace(host="dbc-x.cloud.databricks.com", warehouse_id="wh1")
    run = executor.ProbeRun(info, probe_catalog="workspace", probe_schema="sb_tests", client_factory=fake.factory,
                            manifest_root=tmp_path, record_evidence=False)
    fake.run = run
    fake.existing = {f"semabridge_probe_{run.token}_p3_store_metrics"}
    report = run.run()
    assert report["fixtures"]["p3"]["outcome"] == "inconclusive"
    assert not any("_p3_" in s for s in fake.statements if s.startswith(("CREATE", "DROP")))


def test_preflight_failure_is_inconclusive_and_not_a_completed_proof(tmp_path, monkeypatch):
    fake = FakeDatabricks(preflight_error="connection refused")
    _, report = _run(tmp_path, fake, monkeypatch)
    assert all(g["outcome"] == "inconclusive" for g in report["gates"].values())
    assert not any(s.startswith("CREATE") for s in fake.statements)


def test_cleanup_failure_is_reported(tmp_path, monkeypatch):
    fake = FakeDatabricks(drop_error="warehouse stopped")
    _, report = _run(tmp_path, fake, monkeypatch)
    assert report["cleanup"]["errors"] and not report["cleanup"]["dropped"]


# --- evidence and effective capabilities ------------------------------------------------------

def _record(host, outcomes, *, started, completed, suite=F.SUITE_REVISION, writer=None):
    from semabridge.target_backends.databricks.writer import WRITER_REVISION

    evidence.record(run_id=uuid.uuid4().hex, workspace_host=host, warehouse_id="wh1", probe_schema="w.s",
                    dbsql_version="2026.46", platform={}, suite_revision=suite, writer_revision=writer or WRITER_REVISION,
                    outcomes={"gates": {g: {"outcome": o} for g, o in outcomes.items()}}, cleanup={},
                    started_at=started, completed_at=completed, created_by="t")


ALL_PROVEN = {g: "proven" for g in evidence.GATES}


def test_effective_capabilities_need_proof_and_allow_list():
    host = f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com"
    now = datetime.now(timezone.utc)
    caps, ref = evidence.effective_capabilities(host, "wh1", ["one_to_many_joins"])
    assert caps == frozenset() and "no probe run" in ref["reason"]
    _record(host, ALL_PROVEN, started=now - timedelta(minutes=5), completed=now)
    caps, ref = evidence.effective_capabilities(host, "wh1", [])
    assert caps == frozenset() and ref["not_enabled_by_admin"] == sorted(evidence.OPTIONAL_GATES)
    caps, ref = evidence.effective_capabilities(f"https://{host}/", "wh1", ["one_to_many_joins", "constant_coalesce"])
    assert caps == {"one_to_many_joins", "constant_coalesce"} and ref["status"] == "valid" and ref["run_id"]


def test_admin_list_cannot_force_an_unproven_feature_and_baseline_gates_all():
    host = f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com"
    now = datetime.now(timezone.utc)
    _record(host, {**ALL_PROVEN, "mixed_root_views": "unsupported"}, started=now, completed=now)
    caps, _ = evidence.effective_capabilities(host, "wh1", evidence.OPTIONAL_GATES)
    assert "mixed_root_views" not in caps
    host2 = f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com"
    _record(host2, {**ALL_PROVEN, "baseline": "wrong_result"}, started=now, completed=now)
    caps, ref = evidence.effective_capabilities(host2, "wh1", evidence.OPTIONAL_GATES)
    assert caps == frozenset() and ref["status"] == "baseline_not_proven"


def test_newest_started_run_wins_and_old_or_stale_proof_is_invalid():
    host = f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com"
    now = datetime.now(timezone.utc)
    _record(host, ALL_PROVEN, started=now, completed=now)
    # an OLDER run that finishes later must not replace the newer result
    _record(host, {g: "wrong_result" for g in evidence.GATES}, started=now - timedelta(hours=1),
            completed=now + timedelta(minutes=1))
    assert evidence.effective_capabilities(host, "wh1", evidence.OPTIONAL_GATES)[1]["status"] == "valid"
    old = f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com"
    _record(old, ALL_PROVEN, started=now - timedelta(days=40), completed=now - timedelta(days=40))
    assert "older than" in evidence.effective_capabilities(old, "wh1", evidence.OPTIONAL_GATES)[1]["reason"]
    rev = f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com"
    _record(rev, ALL_PROVEN, started=now, completed=now, suite="0")
    assert "changed" in evidence.effective_capabilities(rev, "wh1", evidence.OPTIONAL_GATES)[1]["reason"]
    unfinished = f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com"
    _record(unfinished, ALL_PROVEN, started=now, completed=None)
    assert "did not complete" in evidence.effective_capabilities(unfinished, "wh1", evidence.OPTIONAL_GATES)[1]["reason"]


def test_recorded_run_reaches_the_plan_settings(tmp_path, monkeypatch):
    """A real probe run (fake Databricks) -> evidence -> the dry run's planner settings."""
    from semabridge.core.engine.targets.databricks import databricks_planner_settings

    fake = FakeDatabricks()
    run, report = _run(tmp_path, fake, monkeypatch, record=True)
    ctx = SimpleNamespace(
        behavior=SimpleNamespace(databricks=SimpleNamespace(planner_calculate_mode="approximate",
                                                            planner_view_mode="separate_views",
                                                            planner_capabilities=["one_to_many_joins"])),
        config=SimpleNamespace(target=SimpleNamespace()), databricks_name_requests=None, source_type="pbix",
        osi_model=SimpleNamespace(metadata={}), pbix_parser="schema",
        source_format=SimpleNamespace(tmsl_definition={"model": {"tables": []}}),
        databricks_target={"host": run.info.host, "warehouse_id": "wh1"},
    )
    s = databricks_planner_settings(ctx)
    assert s.capabilities == {"one_to_many_joins"}
    assert s.evidence["run_id"] == run.run_id and s.evidence["dbsql_version"] == "2026.46"
    assert s.as_dict()["evidence"]["status"] == "valid"


def test_dry_run_never_runs_probes(tmp_path, monkeypatch):
    from semabridge.core.engine.targets import databricks as step

    def boom(*a, **k):
        raise AssertionError("a dry run must never run probes")

    monkeypatch.setattr(executor, "run_probes", boom)
    monkeypatch.setattr(executor.ProbeRun, "run", boom)
    monkeypatch.chdir(tmp_path)
    from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel

    model = SMLModel(unique_name="m", label="m",
                     datasets=[SMLDataset(unique_name="Sales", label="Sales", is_fact=True,
                                          columns=[SMLColumn(unique_name="Amount", label="Amount")])],
                     metrics=[SMLMetric(unique_name="Total", label="Total", dataset="Sales",
                                        expression="SUM(Sales[Amount])", sql_expression="SUM(SALES.`AMOUNT`::DOUBLE)",
                                        sync_enabled=True)])
    ctx = SimpleNamespace(
        sml_model=model, project_id="preview-np", physical_namespace=None, databricks_name_requests=None,
        behavior=SimpleNamespace(databricks=SimpleNamespace(planner_calculate_mode="approximate",
                                                            planner_view_mode="separate_views", planner_capabilities=[])),
        config=SimpleNamespace(target=SimpleNamespace(catalog="workspace", schema="semabridge_dev")),
        source_type="pbix", osi_model=SimpleNamespace(metadata={}), pbix_parser="schema",
        source_format=SimpleNamespace(tmsl_definition={"model": {"tables": []}}), run_id="r",
    )
    step._run_databricks_planner(None, ctx, None)
    assert ctx.databricks_view_plan["settings"]["evidence"]["status"] == "none"
    assert ctx.databricks_view_plan["settings"]["capabilities"] == []


def test_local_allow_list_override_still_requires_proof(monkeypatch):
    from semabridge.core.engine.targets.databricks import CAPABILITY_ALLOW_ENV, capability_allow_list

    behavior = SimpleNamespace(planner_capabilities=[])
    assert capability_allow_list(behavior) == []
    monkeypatch.setenv(CAPABILITY_ALLOW_ENV, "one_to_many_joins, constant_coalesce")
    assert capability_allow_list(behavior) == ["one_to_many_joins", "constant_coalesce"]
    host = f"dbc-{uuid.uuid4().hex[:6]}.cloud.databricks.com"
    caps, ref = evidence.effective_capabilities(host, "wh1", capability_allow_list(behavior))
    assert caps == frozenset() and ref["status"] == "none"            # no proof: still nothing
