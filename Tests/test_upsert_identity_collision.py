"""Upsert identity: separators are significant, equivalent spellings still match.

Real case: one model has both ``RunningMonths`` and ``Running Months`` (deployed as
RUNNINGMONTHS and RUNNING_MONTHS). Matching that ignored underscores collapsed
them into one item, so upsert misreported changes and dropped a removed item
despite the "removed items are kept" policy.
"""
from semabridge.core.upsert_delta import compute_delta, merge_upsert, normalize_name
from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel

A = ("RUNNINGMONTHS", "RunningMonths", "((YEAR(x)-[Year])*12)")
B = ("RUNNING_MONTHS", "Running Months", "IF([Running Year]=1,\"Running 12 Months\")")


def _model(*metrics):
    return SMLModel(unique_name="M", metrics=[
        SMLMetric(unique_name=n, label=lbl, dataset="DATE", expression=e) for n, lbl, e in metrics])


def _table_model(cols):
    return SMLModel(unique_name="M", datasets=[SMLDataset(
        unique_name="DATE", source_table="DATE", columns=[SMLColumn(unique_name=c) for c in cols])])


def test_separators_are_significant_but_spelling_is_not():
    assert normalize_name("RUNNINGMONTHS") != normalize_name("RUNNING_MONTHS")
    assert normalize_name("Running Months") == normalize_name("RUNNING_MONTHS")
    assert normalize_name("Total Units") == normalize_name("TOTAL_UNITS") == normalize_name("total  units")


def test_new_similar_item_is_reported_as_added():
    delta = compute_delta(_model(A, B), _model(A).model_dump())
    assert [e["name"] for e in delta["kinds"]["metrics"]["added"]] == ["Running Months"]
    assert delta["has_changes"]


def test_change_is_attributed_to_the_right_item():
    changed = (B[0], B[1], "IF([Running Year]=1,\"Last 12 Months\")")
    delta = compute_delta(_model(A, changed), _model(A, B).model_dump())
    assert [e["name"] for e in delta["kinds"]["metrics"]["modified"]] == ["Running Months"]
    assert delta["kinds"]["metrics"]["unchanged"] == 1


def test_removed_similar_item_is_kept_on_upsert():
    merged = merge_upsert(_model(A), _model(A, B).model_dump())
    assert sorted(m.unique_name for m in merged.metrics) == ["RUNNINGMONTHS", "RUNNING_MONTHS"]


def test_removed_similar_column_is_kept_on_upsert():
    merged = merge_upsert(_table_model(["RUNNINGMONTHS"]), _table_model(["RUNNINGMONTHS", "RUNNING_MONTHS"]).model_dump())
    assert sorted(c.unique_name for c in merged.datasets[0].columns) == ["RUNNINGMONTHS", "RUNNING_MONTHS"]


def test_equivalent_spelling_is_not_duplicated():
    new = _model(("TOTAL_UNITS", "Total Units", "SUM([Units])"))
    old = _model(("Total Units", "Total Units", "SUM([Units])")).model_dump()
    merged = merge_upsert(new, old)
    assert [m.unique_name for m in merged.metrics] == ["TOTAL_UNITS"]


def test_collisions_are_reported_not_hidden():
    # Two names that normalize identically in one model must be surfaced.
    new = _model(("TOTAL_UNITS", "Total Units", "SUM([Units])"), ("Total Units", "Total Units", "SUM([U2])"))
    delta = compute_delta(new, None)
    assert delta["identity_collisions"] and delta["identity_collisions"][0]["kind"] == "metrics"
    assert delta["has_changes"]


def test_collision_in_deployed_model_does_not_redeploy_forever():
    # Baseline and source both contain the colliding pair; nothing changed.
    pair = (("TOTAL_UNITS", "Total Units", "SUM([Units])"), ("Total Units", "Total Units", "SUM([Units])"))
    delta = compute_delta(_model(*pair), _model(*pair).model_dump())
    assert delta["identity_collisions"]  # still reported
    assert not delta["has_changes"]      # but not a change -> upsert can skip


def test_merge_never_emits_two_items_with_the_same_identity():
    # Baseline holds both spellings; source keeps one. Both would deploy as
    # TOTAL_UNITS, so the merge must not add the other back.
    new = _model(("Total Units", "Total Units", "SUM([Units])"))
    old = _model(("Total Units", "Total Units", "SUM([Units])"), ("TOTAL_UNITS", "Total Units", "SUM([Units])")).model_dump()
    merged = merge_upsert(new, old)
    assert [m.unique_name for m in merged.metrics] == ["Total Units"]
