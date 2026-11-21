"""Stored metric SQL never keeps DDL clauses; real SQL is never cut."""
import pytest

from semabridge.ontology.models import strip_ddl_clauses as strip

CUT = [
    ("SUM(T.X) WITH SYNONYMS = ('rev')", "SUM(T.X)"),
    ("SUM(T.X) WITH SYNONYMS=('a','b')", "SUM(T.X)"),
    ("SUM(T.X) with synonyms = ('a')", "SUM(T.X)"),
    ("SUM(T.X) WITH SYNONYMS = ('revenue (net)', 'rev')", "SUM(T.X)"),
    ("SUM(T.X) COMMENT = 'total'", "SUM(T.X)"),
    ("SUM(T.X) COMMENT='it''s total'", "SUM(T.X)"),
    ('SUM(T.X) COMMENT = "x"', "SUM(T.X)"),
    ("SUM(T.X) WITH SYNONYMS=('a') COMMENT='c'", "SUM(T.X)"),
    ("SUM(T.X) COMMENT='c' WITH SYNONYMS=('a')", "SUM(T.X)"),
    ("SUM(T.X) WITH SYNONYMS=('a'),", "SUM(T.X)"),
    ("SUM(T.X)\n  WITH SYNONYMS = ('a')\n  COMMENT = 'multi\nline'", "SUM(T.X)"),
    ("SUM(T.X) COMMENT = 'has WITH SYNONYMS inside'", "SUM(T.X)"),
    ("SUM(T.X) WITH SYNONYMS = ('o''brien')", "SUM(T.X)"),
    ("SUM(T.X) WITH SYNONYMS = ('a)b')", "SUM(T.X)"),
]
KEEP = [
    "SUM(T.X)", "SUM(CASE WHEN T.N = ' comment = ''x''' THEN 1 END)",
    "SUM(CASE WHEN T.N = 'with synonyms = (a)' THEN 1 END)", 'SUM("COMMENT")', "SUM(T.COMMENT_COUNT)",
    "COUNT(T.WITH_SYNONYMS)", "SUM(T.X) / NULLIF(SUM(T.Y), 0)",
    "SUM(T.X) OVER (PARTITION BY EXCLUDING P.A)", "CASE WHEN T.A = 'WITH' THEN 1 END",
]


@pytest.mark.parametrize("raw,clean", CUT)
def test_ddl_clauses_are_removed(raw, clean):
    assert strip(raw) == clean


@pytest.mark.parametrize("raw", KEEP)
def test_plain_sql_is_unchanged(raw):
    assert strip(raw) == raw


@pytest.mark.parametrize("v", [None, 1, 2.5, [], {}])
def test_non_strings_pass_through(v):
    assert strip(v) == v
