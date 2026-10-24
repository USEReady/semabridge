"""
Suite 3 - acceptance on REAL generated DDL (skipped unless env vars are set).

  ACCEPT_BATCH_CSV  batch file (model,pbix,ddl) for relationship_inventory
  ACCEPT_BASELINE   baseline.json saved in Phase A

Pass = every model: no relationship got WORSE vs baseline (MISSING/key NO may only improve),
and every DDL passes the static validator (no missing keys, duplicate paths or cycles).
"""
import csv
import json
import os

import pytest

BATCH, BASE = os.environ.get("ACCEPT_BATCH_CSV"), os.environ.get("ACCEPT_BASELINE")
pytestmark = pytest.mark.skipif(not (BATCH and BASE), reason="set ACCEPT_BATCH_CSV and ACCEPT_BASELINE")

RANK = {"MISSING": 0, "AMBIGUOUS": 1, "EMITTED_DIFFERENT": 2, "EMITTED_REVERSED": 3, "EMITTED": 3}


def _jobs():
    with open(BATCH, newline="", encoding="utf-8") as fh:
        return [(r["model"], r["pbix"], r["ddl"]) for r in csv.DictReader(fh)]


@pytest.mark.parametrize("model,pbix,ddl", _jobs() if BATCH else [])
def test_no_relationship_regressed(model, pbix, ddl):
    from relationship_inventory import reconcile
    rows, _, _ = reconcile(pbix, open(ddl, encoding="utf-8").read())
    old = {r["source_relationship"]: r for r in json.load(open(BASE, encoding="utf-8")).get(model, [])}
    worse = []
    for r in rows:
        o = old.get(r["source_relationship"])
        if not o:
            continue
        if RANK[r["outcome"]] < RANK[o["outcome"]]:
            worse.append(f"{r['source_relationship']}: {o['outcome']} -> {r['outcome']}")
        if o["key_on_referenced_side"] == "yes" and r["key_on_referenced_side"] == "NO":
            worse.append(f"{r['source_relationship']}: key yes -> NO")
    assert not worse, "\n".join(worse)


@pytest.mark.parametrize("model,pbix,ddl", _jobs() if BATCH else [])
def test_ddl_static_validation(model, pbix, ddl):
    from validate_semantic_view import Report, parse_ddl, static_checks
    tables, rels = parse_ddl(open(ddl, encoding="utf-8").read())
    rep = Report()
    static_checks(tables, rels, rep)
    fails = [f"{r[1]} {r[2]}: {r[3]}" for r in rep.rows if r[0] == "FAIL"]
    assert not fails, "\n".join(fails)
