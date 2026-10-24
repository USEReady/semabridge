"""Suite 1 - relationship normalisation (Pass 3 cycle / duplicate pruning). No names hardcoded in code under test."""


def ident(r):
    return (r.from_dataset, tuple(r.from_columns), r.to_dataset, tuple(r.to_columns))


def kept_ids(kept):
    return {ident(r) for r in kept}


def test_every_relationship_is_accounted_for(normalize, R):
    rels = [R("F", "a", "D1", "a"), R("F", "b", "D2", "b"), R("D1", "x", "D2", "x", active=False)]
    kept, decisions = normalize(rels)
    if decisions is not None:
        assert len(decisions) == len(rels)
    assert len(kept) <= len(rels)


def test_inactive_edge_is_not_a_path_for_pruning(normalize, R):
    # A->B (active), B->C (INACTIVE), A->C (active). With inactive edges ignored there is no
    # alternate path, so the active A->C must be kept. (The Sales & Returns bug pattern.)
    rels = [R("A", "b", "B", "b"), R("B", "c", "C", "c", active=False, card="one-to-one"),
            R("A", "c", "C", "c2")]
    kept, _ = normalize(rels)
    assert ("A", ("c",), "C", ("c2",)) in kept_ids(kept)


def test_inactive_relationship_is_never_dropped(normalize, R):
    rels = [R("A", "b", "B", "b"), R("B", "c", "C", "c", active=False), R("A", "c", "C", "c")]
    kept, _ = normalize(rels)
    assert ("B", ("c",), "C", ("c",)) in kept_ids(kept)


def test_same_table_pair_different_columns_both_kept(normalize, R):
    rels = [R("F", "order_date", "Dates", "d"), R("F", "ship_date", "Dates", "d", active=False)]
    kept, _ = normalize(rels)
    assert len(kept_ids(kept)) == 2


def test_exact_duplicate_removed_once(normalize, R):
    rels = [R("F", "k", "D", "k", name="r1"), R("F", "k", "D", "k", name="r2")]
    kept, _ = normalize(rels)
    assert len(kept_ids(kept)) == 1 and len(kept) == 1


def test_true_active_diamond_not_silently_dropped(normalize, R):
    # F->S, S->R, F->R all ACTIVE: a real second path. Must not disappear without a decision.
    rels = [R("F", "s", "S", "s"), R("S", "r", "R", "r"), R("F", "r", "R", "r")]
    kept, decisions = normalize(rels)
    if ("F", ("r",), "R", ("r",)) not in kept_ids(kept):
        assert decisions is not None, "dropped with no decision record"
        reasons = [d for d in decisions if d.get("decision", "").startswith("dropped")]
        assert reasons and all(d.get("reason") for d in reasons)


def test_plain_star_schema_unchanged(normalize, R):
    rels = [R("Fact", "d1", "Dim1", "d1"), R("Fact", "d2", "Dim2", "d2"), R("Fact", "d3", "Dim3", "d3")]
    kept, _ = normalize(rels)
    assert kept_ids(kept) == {ident(r) for r in rels}
