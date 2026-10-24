"""Suite 2 - key resolution (PK / UNIQUE / alias / reject). No names hardcoded in code under test."""


def plan_for(plan_keys, rels, aliases, pks, unique=None, cols=None):
    unique = unique or {}
    return plan_keys(rels, aliases, pks, cols or {},
                     is_unique=lambda ds, c: unique.get((ds, tuple(x.upper() for x in c))))


def dec(plan, src_from, src_to):
    return next(d for d in plan.decisions if d.source.startswith(f"{src_from}[") and f"-> {src_to}[" in d.source)


def test_referenced_fact_column_verified_unique_gets_pk(plan_keys, R):
    rels = [R("Detail", "ID", "Fact", "ID", card="one-to-one"), R("Fact", "dk", "Dim", "dk")]
    p = plan_for(plan_keys, rels, {"Detail": "DETAIL", "Fact": "FACT", "Dim": "DIM"},
                 {"DIM": ["DK"]}, unique={("Fact", ("ID",)): True})
    assert dec(p, "Detail", "Fact").decision == "emit_add_primary_key"
    assert p.keys["FACT"].primary_key == ["ID"]


def test_second_referenced_column_gets_unique(plan_keys, R):
    rels = [R("A", "pid", "Lk", "PID"), R("B", "pname", "Lk", "PNAME")]
    p = plan_for(plan_keys, rels, {"A": "A", "B": "B", "Lk": "LK"}, {"LK": ["PID"]},
                 unique={("Lk", ("PID",)): True, ("Lk", ("PNAME",)): True})
    assert dec(p, "B", "Lk").decision == "emit_add_unique_key"
    assert ["PNAME"] in p.keys["LK"].unique_keys and p.keys["LK"].primary_key == ["PID"]


def test_non_unique_referenced_column_rejected_with_reason(plan_keys, R):
    rels = [R("A", "x", "B", "X")]
    p = plan_for(plan_keys, rels, {"A": "A", "B": "B"}, {}, unique={("B", ("X",)): False})
    d = dec(p, "A", "B")
    assert d.decision == "reject" and "not unique" in d.reason
    assert p.keys["B"].primary_key == []


def test_unverifiable_key_rejected_not_assumed(plan_keys, R):
    rels = [R("A", "x", "B", "X", card="one-to-one")]
    p = plan_for(plan_keys, rels, {"A": "A", "B": "B"}, {})          # is_unique -> None
    d = dec(p, "A", "B")
    assert d.decision == "reject" and "rejected_unverified_key" in d.reason


def test_inactive_goes_to_alias_original_table_unchanged(plan_keys, R):
    rels = [R("P", "name", "Lk", "NAME", active=False, card="one-to-one")]
    p = plan_for(plan_keys, rels, {"P": "P", "Lk": "LK"}, {"LK": ["PID"]},
                 unique={("Lk", ("NAME",)): True})
    d = dec(p, "P", "Lk")
    assert d.decision == "emit_via_alias" and d.to_logical != "LK"
    alias = p.keys[d.to_logical]
    assert alias.is_alias_of == "LK" and alias.primary_key == ["NAME"]
    assert p.keys["LK"].primary_key == ["PID"] and p.keys["LK"].unique_keys == []


def test_composite_key_supported(plan_keys, R):
    rels = [R("F", ["a", "b"], "D", ["A", "B"])]
    p = plan_for(plan_keys, rels, {"F": "F", "D": "D"}, {}, unique={("D", ("A", "B")): True})
    assert p.keys["D"].primary_key == ["A", "B"]


def test_many_to_one_star_needs_no_new_keys(plan_keys, R):
    rels = [R("F", "d1", "D1", "D1"), R("F", "d2", "D2", "D2")]
    p = plan_for(plan_keys, rels, {"F": "F", "D1": "D1", "D2": "D2"}, {"D1": ["D1"], "D2": ["D2"]})
    assert all(d.decision == "emit" for d in p.decisions)
    assert p.keys["F"].primary_key == []
