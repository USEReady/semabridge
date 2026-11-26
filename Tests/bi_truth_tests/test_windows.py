"""2. Time windows on a case.

A window narrows a case to one period and carries its own expected value. A case
with no windows must behave exactly as it did before windows existed.
"""
from __future__ import annotations

from bi_truth_tests import adapter

W = adapter.names(
    adapter.windows,
    "parse_case_windows", "build_windowed_sql", "CaseWindow", "is_semantic_view_query",
)


# ------------------------------------------------------------------ parsing

def test_2_1_windows_parse_from_a_list(window_json):
    wins = W.parse_case_windows(window_json)
    assert [w.window_key for w in wins] == ["w1", "w2", "w3"]


def test_2_2_windows_parse_from_stored_json(window_json):
    import json
    wins = W.parse_case_windows(json.dumps(window_json))
    assert len(wins) == 3


def test_2_3_no_windows_gives_an_empty_list():
    for raw in (None, [], "", "[]", {}, "not json"):
        assert W.parse_case_windows(raw) == []


def test_2_4_an_entry_without_a_key_is_skipped():
    wins = W.parse_case_windows([
        {"window_label": "no key here", "filter_clause": "X = 1"},
        {"window_key": "w1", "filter_clause": "X = 2"},
    ])
    assert [w.window_key for w in wins] == ["w1"]


def test_2_5_duplicate_keys_are_collapsed():
    wins = W.parse_case_windows([
        {"window_key": "w1", "filter_clause": "X = 1"},
        {"window_key": "W1", "filter_clause": "X = 2"},
    ])
    assert len(wins) == 1


def test_2_6_a_window_knows_whether_it_has_an_expected_value():
    with_value = W.parse_case_windows([{"window_key": "a", "expected_value": 10}])[0]
    without = W.parse_case_windows([{"window_key": "b"}])[0]
    assert with_value.has_expected_value
    assert not without.has_expected_value


# ------------------------------------------------------------------ building SQL

def test_2_7_a_filter_is_added_to_a_plain_select(plain_sql):
    out = W.build_windowed_sql(plain_sql, "PERIOD = 1")
    assert "WHERE" in out.upper()
    assert "PERIOD = 1" in out


def test_2_8_an_existing_where_gets_an_and():
    sql = "SELECT SUM(AMOUNT) FROM T1 WHERE REGION = 'A'"
    out = W.build_windowed_sql(sql, "PERIOD = 1")
    assert out.upper().count("WHERE") == 1
    assert " AND " in out.upper()


def test_2_9_the_filter_goes_before_order_by():
    sql = "SELECT A, SUM(B) FROM T1 GROUP BY A ORDER BY A"
    out = W.build_windowed_sql(sql, "PERIOD = 1")
    assert out.upper().index("PERIOD") < out.upper().index("ORDER BY")


def test_2_10_the_filter_goes_before_limit():
    sql = "SELECT A FROM T1 LIMIT 10"
    out = W.build_windowed_sql(sql, "PERIOD = 1")
    assert out.upper().index("PERIOD") < out.upper().index("LIMIT")


def test_2_11_no_filter_returns_the_sql_unchanged(plain_sql):
    assert W.build_windowed_sql(plain_sql, "").strip() == plain_sql.strip()


def test_2_12_no_sql_returns_nothing():
    assert W.build_windowed_sql(None, "PERIOD = 1") is None
    assert W.build_windowed_sql("", "PERIOD = 1") is None


# ------------------------------------------------------------------ semantic views

def test_2_13_a_semantic_view_query_is_recognised(semantic_view_sql, plain_sql):
    assert W.is_semantic_view_query(semantic_view_sql)
    assert not W.is_semantic_view_query(plain_sql)


def test_2_14_a_semantic_view_query_is_filtered_inside_the_call(semantic_view_sql):
    # The spike proved the internal WHERE works and narrows the metric.
    out = W.build_windowed_sql(semantic_view_sql, "DIM_ONE.PERIOD = 1")
    assert out is not None
    assert "WHERE DIM_ONE.PERIOD = 1" in out
    # Filter lands inside the brackets, not after
    assert out.endswith(")")
    # Query with DIMENSIONS keeps them and gains the filter
    assert "DIMENSIONS DIM_ONE" in out
    # A filter on a dimension not in DIMENSIONS is still injected, since D1 shows that works
    out_non_projected = W.build_windowed_sql(semantic_view_sql, "COL_DATE.YEAR = 2014")
    assert out_non_projected is not None
    assert "WHERE COL_DATE.YEAR = 2014" in out_non_projected
    assert "DIMENSIONS DIM_ONE" in out_non_projected
    assert out_non_projected.endswith(")")


def test_2_15_a_windowed_query_is_distinguishable_from_an_unchanged_one(semantic_view_sql, plain_sql):
    windowed = W.build_windowed_sql(semantic_view_sql, "DIM_ONE.PERIOD = 1")
    unchanged = W.build_windowed_sql(semantic_view_sql, "")
    assert windowed is not None
    assert windowed != unchanged
    assert W.build_windowed_sql(plain_sql, "") is not None


def test_2_16_semantic_view_unqualified_filter_is_refused(semantic_view_sql):
    """An unqualified filter on a SEMANTIC_VIEW is refused because the warehouse rejects it."""
    assert W.build_windowed_sql(semantic_view_sql, "PERIOD = 1") is None


def test_2_17_complex_or_unsafe_filter_is_refused(semantic_view_sql, plain_sql):
    """Complex or SQL-injection style filters are refused for all query shapes."""
    assert W.build_windowed_sql(semantic_view_sql, "DIM_ONE.PERIOD = 1; DROP TABLE T") is None
    assert W.build_windowed_sql(semantic_view_sql, "DIM_ONE.PERIOD IN (SELECT ID FROM T)") is None
    assert W.build_windowed_sql(plain_sql, "PERIOD = 1; DELETE FROM T") is None


def test_2_18_unrecognised_query_shape_is_refused():
    """Query shapes not recognised as plain SELECT or SEMANTIC_VIEW are refused."""
    assert W.build_windowed_sql("UPDATE T SET X = 1", "T.PERIOD = 1") is None
    assert W.build_windowed_sql("INSERT INTO T VALUES (1)", "T.PERIOD = 1") is None
