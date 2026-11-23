"""Upsert identity: equivalent spellings match; distinct objects never merge or disappear."""
import pytest

from semabridge.core.upsert_delta import compute_delta, merge_upsert, normalize_name
from semabridge.sml.models import SMLColumn, SMLDataset, SMLMetric, SMLModel

SAME = [
    ("Total Units", "TOTAL_UNITS"), ("total units", "Total Units"), ("Total  Units", "Total Units"),
    (" Total Units ", "Total Units"), ("Total\tUnits", "Total Units"), ("TOTAL__UNITS", "TOTAL_UNITS"),
    ("Revenue YTD", "REVENUE_YTD"), ("a", "A"), ("Sales 2024", "SALES_2024"), ("ÄRGER", "ärger"),
    ("Margin %", "margin %"), ("Cases MoM%", "CASES MOM%"), ("x_y", "X Y"),
    ("Straße", "STRASSE"),
]
DIFFERENT = [
    ("RunningMonths", "Running Months"), ("RUNNINGMONTHS", "RUNNING_MONTHS"), ("Total", "Totals"),
    ("Units YTD", "Units MTD"), ("AB", "A B"), ("CASECOUNT", "CASE_COUNT"), ("Revenue1", "Revenue 1"),
    ("New Hires", "NewHires"), ("Sales-Amount", "Sales Amount"), ("Sales.Amount", "Sales Amount"),
    ("Cost", "Cost%"), ("Net", "Net."), ("YoY", "Yo Y"), ("ID", "I D"),
]


@pytest.mark.parametrize("a,b", SAME)
def test_equivalent_spellings_share_one_identity(a, b):
    assert normalize_name(a) == normalize_name(b)


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_distinct_names_keep_distinct_identities(a, b):
    assert normalize_name(a) != normalize_name(b)


def _m(*items):
    return SMLModel(unique_name="M", metrics=[SMLMetric(unique_name=n, label=n, dataset="T", expression=e) for n, e in items])


def _cols(*names):
    return SMLModel(unique_name="M", datasets=[SMLDataset(unique_name="T", source_table="T",
                    columns=[SMLColumn(unique_name=n) for n in names])])


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_removed_distinct_metric_is_kept_on_upsert(a, b):
    merged = merge_upsert(_m((a, "SUM(X)")), _m((a, "SUM(X)"), (b, "SUM(Y)")).model_dump())
    assert sorted(x.unique_name for x in merged.metrics) == sorted([a, b])


# Distinct names whose PUBLISHED (deployed) column names clash, e.g. both become SALES_AMOUNT.
# Keeping both would create duplicate columns and the view would fail to create, so the removed
# baseline column is skipped (rule restored after review: "skip the old column whenever the published
# names clash").
PUBLISHED_CLASH = {("Sales-Amount", "Sales Amount"), ("Sales.Amount", "Sales Amount"), ("Net", "Net.")}


@pytest.mark.parametrize("a,b", [p for p in DIFFERENT if p not in PUBLISHED_CLASH])
def test_removed_distinct_column_is_kept_on_upsert(a, b):
    merged = merge_upsert(_cols(a), _cols(a, b).model_dump())
    assert sorted(c.unique_name for c in merged.datasets[0].columns) == sorted([a, b])


@pytest.mark.parametrize("a,b", sorted(PUBLISHED_CLASH))
def test_removed_column_with_clashing_published_name_is_skipped(a, b):
    merged = merge_upsert(_cols(a), _cols(a, b).model_dump())
    assert [c.unique_name for c in merged.datasets[0].columns] == [a]


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_new_distinct_metric_is_reported_as_added(a, b):
    d = compute_delta(_m((a, "SUM(X)"), (b, "SUM(Y)")), _m((a, "SUM(X)")).model_dump())
    assert [e["name"] for e in d["kinds"]["metrics"]["added"]] == [b]


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_change_is_attributed_to_the_changed_metric(a, b):
    d = compute_delta(_m((a, "SUM(X)"), (b, "SUM(Z)")), _m((a, "SUM(X)"), (b, "SUM(Y)")).model_dump())
    assert [e["name"] for e in d["kinds"]["metrics"]["modified"]] == [b]


@pytest.mark.parametrize("a,b", SAME)
def test_equivalent_spelling_is_not_duplicated(a, b):
    merged = merge_upsert(_m((b, "SUM(X)")), _m((a, "SUM(X)")).model_dump())
    assert len(merged.metrics) == 1


@pytest.mark.parametrize("n", [1, 5, 50])
def test_identical_model_has_no_changes(n):
    items = [(f"M{i}", f"SUM(X{i})") for i in range(n)]
    assert compute_delta(_m(*items), _m(*items).model_dump())["has_changes"] is False


def test_empty_baseline_reports_everything_added():
    d = compute_delta(_m(("A", "SUM(X)"), ("B", "SUM(Y)")), None)
    assert sorted(e["name"] for e in d["kinds"]["metrics"]["added"]) == ["A", "B"]
