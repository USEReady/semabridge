"""Test the central date-function sanitizer imported directly from snowflake_emitter."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Import the sanitizer directly
from semabridge.connectors.snowflake_emitter import _sanitize_snowflake_date_functions as sanitize

tests = [
    # DATE_PART cases
    ("DATE_PART(YEAR, col)",             "DATE_PART('year', col)"),
    ('DATE_PART("YEAR", col)',           "DATE_PART('year', col)"),
    ("DATE_PART('YEAR', col)",           "DATE_PART('year', col)"),
    ("DATE_PART( YEAR , col)",           "DATE_PART('year', col)"),
    # DATE_TRUNC cases
    ("DATE_TRUNC(YEAR, MAX_DATE)",       "DATE_TRUNC('year', MAX_DATE)"),
    ('DATE_TRUNC("YEAR", MAX_DATE)',     "DATE_TRUNC('year', MAX_DATE)"),
    ("DATE_TRUNC('YEAR', MAX_DATE)",     "DATE_TRUNC('year', MAX_DATE)"),
    # DATEADD cases
    ("DATEADD(YEAR, -1, MAX_DATE)",      "DATEADD('year', -1, MAX_DATE)"),
    ('DATEADD("YEAR", -1, MAX_DATE)',    "DATEADD('year', -1, MAX_DATE)"),
    ("DATEADD('YEAR', -1, MAX_DATE)",    "DATEADD('year', -1, MAX_DATE)"),
    # DATEDIFF cases
    ("DATEDIFF(MONTH, a, b)",            "DATEDIFF('month', a, b)"),
    # Mixed compound expression (the real-world failing case)
    (
        "SUM(CASE WHEN DATE_PART(YEAR, col) = DATE_PART(YEAR, DATEADD(YEAR, -1, MAX_DATE)) "
        "AND col BETWEEN DATEADD(YEAR, -1, DATE_TRUNC(YEAR, MAX_DATE)) AND DATEADD(YEAR, -1, MAX_DATE) THEN val END)",
        "SUM(CASE WHEN DATE_PART('year', col) = DATE_PART('year', DATEADD('year', -1, MAX_DATE)) "
        "AND col BETWEEN DATEADD('year', -1, DATE_TRUNC('year', MAX_DATE)) AND DATEADD('year', -1, MAX_DATE) THEN val END)"
    ),
]

all_ok = True
for inp, expected in tests:
    got = sanitize(inp)
    ok = (got == expected)
    if not ok:
        all_ok = False
    print(f"{'PASS' if ok else 'FAIL'}: {repr(inp[:60])}")
    if not ok:
        print(f"  GOT:      {repr(got)}")
        print(f"  EXPECTED: {repr(expected)}")

print()
print("✅ ALL TESTS PASSED" if all_ok else "❌ SOME TESTS FAILED")
sys.exit(0 if all_ok else 1)
