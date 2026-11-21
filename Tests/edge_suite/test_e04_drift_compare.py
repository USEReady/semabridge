"""Drift comparison: formatting never drifts; meaning always does. NULL placeholders exact."""
import pytest

from semabridge.governance.drift_service import _is_null_placeholder, _normalize_metric_expr as norm

FORMAT_ONLY = [
    ("SUM(T.X)", 'SUM("T"."X")'), ("SUM(T.X)", "sum(t.x)"), ("SUM(T.X)", "SUM( T.X )"),
    ("SUM(T.X)", "SUM(T.X) -- note"), ("SUM(T.X)", "SUM(T.X) /* c */"), ("SUM(T.X)", "\n SUM(T.X)\n"),
    ("SUM(T.X)/NULLIF(SUM(T.Y),0)", "SUM(T.X) / NULLIF(SUM(T.Y), 0)"),
    ("CASE WHEN T.A=1 THEN 1 END", "case when t.a = 1 then 1 end"),
    ("COUNT(DISTINCT T.ID)", "count( distinct t.id )"), ("T.X::FLOAT", 'T."X"::FLOAT'),
    ("SUM(T.X)*100", "SUM(T.X) * 100"), ("A.B.C", '"A"."B"."C"'), ("IFF(T.A>0,1,0)", "IFF( T.A > 0 , 1 , 0 )"),
    ("COALESCE(SUM(T.X),0)", "coalesce(sum(t.x), 0)"), ("SUM(T.X)", "SUM(T.X)\t"),
]
MEANING = [
    ("SUM(T.X)", "AVG(T.X)"), ("SUM(T.X)", "SUM(T.Y)"), ("SUM(T.X)", "SUM(U.X)"), ("SUM(T.X)", "SUM(T.X) * 1.18"),
    ("SUM(T.X)", "SUM(DISTINCT T.X)"), ("COUNT(T.ID)", "COUNT(DISTINCT T.ID)"), ("T.X::FLOAT", "T.X::INT"),
    ("CAST(T.A AS NUMBER)", "CAST(T.A AS VARCHAR)"), ("T.S = 'Won'", "T.S = 'won'"), ("T.S = 'Won'", "T.S = 'Lost'"),
    ("SUM(T.X) * 0.75", "SUM(T.X) * 0.7"), ("T.A > 0", "T.A >= 0"), ("T.A - T.B", "T.B - T.A"),
    ("SUM(T.X) / SUM(T.Y)", "SUM(T.Y) / SUM(T.X)"), ("NULLIF(SUM(T.Y),0)", "NULLIF(SUM(T.Y),1)"),
    ("CASE WHEN T.A IS NULL THEN 1 END", "CASE WHEN T.A IS NOT NULL THEN 1 END"),
    ("SUM(CASE WHEN T.S='Open' THEN T.V END)", "SUM(T.V)"), ("'a b'", "'a  b'"), ("'x -- y'", "'x'"),
    ('"Col A"', '"Col B"'), ("T.X OR T.Y", "T.X AND T.Y"), ("MIN(T.X)", "MAX(T.X)"), ("T.X / 100", "T.X / 10"),
    ("SUM(T.X) OVER (PARTITION BY EXCLUDING P.A)", "SUM(T.X) OVER (PARTITION BY EXCLUDING P.B)"),
    ("DATEADD(YEAR,-1,T.D)", "DATEADD(YEAR,-2,T.D)"), ("ROUND(T.X,2)", "ROUND(T.X,0)"),
]


@pytest.mark.parametrize("a,b", FORMAT_ONLY)
def test_formatting_differences_compare_equal(a, b):
    assert norm(a) == norm(b), (norm(a), norm(b))


@pytest.mark.parametrize("a,b", MEANING)
def test_meaningful_differences_never_compare_equal(a, b):
    assert norm(a) != norm(b), norm(a)


@pytest.mark.parametrize("e", [x for p in FORMAT_ONLY + MEANING for x in p])
def test_normalisation_is_idempotent(e):
    assert norm(norm(e)) == norm(e)


@pytest.mark.parametrize("e", ["NULL", "null", " NULL ", "CAST(NULL AS DOUBLE)", "cast( null as number )",
                               "CAST(NULL AS NUMBER(38,2))", "NULL::FLOAT", "NULL::VARCHAR", "CAST(NULL AS VARCHAR(10))"])
def test_true_null_placeholders_are_detected(e):
    assert _is_null_placeholder(e)


@pytest.mark.parametrize("e", ["SUM(T.X)/NULLIF(SUM(T.Y),0)", "CASE WHEN T.A IS NULL THEN 1 END",
                               "COALESCE(SUM(T.X), NULL)", "SUM(CASE WHEN T.S='x' THEN T.V ELSE NULL END)",
                               "NULLIF(T.A, 0)", "T.NULLABLE_FLAG", "'NULL'", "", None, "CAST(T.NULL_COL AS INT)",
                               "NULL + 1", "IFNULL(T.A, 0)"])
def test_expressions_merely_containing_null_are_not_placeholders(e):
    assert not _is_null_placeholder(e)
