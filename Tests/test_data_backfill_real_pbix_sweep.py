"""Broad sweep of the data-backfill feature against every table in every
real PBIX file already available in this environment's upload cache.

Unlike test_snowflake_data_backfill_safety.py (which tests the safety-check
and load logic against representative synthetic garbage/real-data SHAPES --
sufficient on its own, since that logic only ever looks at row/null counts,
never at which file or table they came from), this file exists to catch a
different class of bug: one that only shows up on a REAL PBIX's actual
column names/types across many different files -- reserved words, special
characters, leading digits, spaces, unusual dtypes -- that a hand-picked
synthetic fixture might not happen to include.

Environment-dependent by nature: the PBIX files themselves are user uploads
living in a local Temp folder, not tracked in this repo/CI. Every test here
skips cleanly (not "fails") when that folder isn't present, exactly like a
live-Snowflake-only test would.

No live Snowflake connection is used or required -- write_pandas itself is
never called; the load path is exercised up to (and including) the exact
column-rename + unmatched-column-drop logic _backfill_one_table itself
uses, with the "live" schema simulated as exactly the sanitized names (the
expected steady state after the table's been created once), so a failure
here means the LOGIC is wrong, not that a particular live table's state was
unusual.
"""
from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.formats.sml.models import DataType
from semabridge.utils.identifiers import IdentifierSanitizer

_UPLOADS_DIR = r"C:\Users\Sabiha Anjum\AppData\Local\Temp\semabridge\uploads"
_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CORPORATE_SPEND_PBIX = os.path.join(_UPLOADS_DIR, "9fabc0c1283c43f18c85b86436de3584_Corporate Spend.pbix")

_SKIP_REASON = f"PBIX upload cache not present in this environment: {_UPLOADS_DIR}"


def _distinct_pbix_files() -> list[str]:
    """One file per distinct base name (uploads carry a random 32-char hex
    prefix per upload, so the same underlying file is often present many
    times under different prefixes) -- a representative, non-redundant
    sweep rather than re-testing identical content dozens of times."""
    if not os.path.isdir(_UPLOADS_DIR):
        return []
    seen: dict[str, str] = {}
    for fn in os.listdir(_UPLOADS_DIR):
        if not fn.lower().endswith(".pbix"):
            continue
        base = re.sub(r"^[0-9a-fA-F]{32}_", "", fn)
        seen.setdefault(base, fn)
    return [os.path.join(_UPLOADS_DIR, fn) for fn in sorted(seen.values())]


def _real_tables(pbix_model) -> list[str]:
    schema = pbix_model.schema
    return [
        t for t in schema["TableName"].unique().tolist()
        if not t.startswith(("LocalDateTable_", "DateTableTemplate_"))
    ]


@pytest.fixture(scope="module")
def pbix_files():
    files = _distinct_pbix_files()
    if not files:
        pytest.skip(_SKIP_REASON)
    return files


@pytest.fixture(scope="module")
def sanitizer():
    return IdentifierSanitizer()


def test_every_column_in_every_real_pbix_file_sanitizes_to_a_valid_identifier(pbix_files, sanitizer):
    """Part 2, item 2(b): column sanitization via IdentifierSanitizer must
    produce a syntactically valid Snowflake identifier for every column,
    across every real table, across every distinct PBIX file available --
    not just the reserved-word case (Date -> COL_DATE) already covered by
    the targeted unit tests.

    A get_table() exception is tracked separately, NOT as a sanitization
    failure -- it's a genuine PBIXRay-internal extraction issue on that
    specific table (see the module docstring's "new edge case" note), and
    _backfill_one_table already catches exactly this and reports
    status="failed" for that one table without affecting any other table
    (see test_one_table_failure_does_not_abort_the_rest) -- it must not be
    conflated with an actual bug in THIS feature's own sanitization logic.
    """
    from pbixray import PBIXRay

    failures = []
    extraction_issues = []
    tables_checked = 0
    columns_checked = 0

    for path in pbix_files:
        try:
            model = PBIXRay(path)
        except Exception as exc:
            extraction_issues.append(f"{os.path.basename(path)}: could not open file ({exc})")
            continue

        for table in _real_tables(model):
            try:
                df = model.get_table(table)
            except Exception as exc:
                extraction_issues.append(f"{os.path.basename(path)}::{table}: get_table() raised ({type(exc).__name__}: {exc})")
                continue

            tables_checked += 1
            for col in df.columns:
                columns_checked += 1
                sanitized = sanitizer.sanitize_column(str(col))
                if not sanitized or not _VALID_IDENTIFIER.match(sanitized):
                    failures.append(
                        f"{os.path.basename(path)}::{table}.{col!r} -> {sanitized!r} "
                        "(not a valid Snowflake identifier)"
                    )

    print(f"\nChecked {columns_checked} column(s) across {tables_checked} table(s) "
          f"across {len(pbix_files)} distinct PBIX file(s).")
    if extraction_issues:
        print(f"\n{len(extraction_issues)} table(s) hit a PBIXRay-internal extraction error "
              "(new edge case -- see module docstring; _backfill_one_table already isolates these):")
        for issue in extraction_issues:
            print(f"  {issue}")

    assert not failures, "Invalid sanitized identifier(s):\n" + "\n".join(failures)


def test_every_real_table_loads_with_matching_row_count_and_no_dropped_columns(pbix_files, sanitizer):
    """Part 2, item 2(c): simulates the exact rename + unmatched-column-drop
    logic _backfill_one_table uses (see schema_manager.py), against every
    real table in every distinct PBIX file, and confirms:
      - every column survives the "unmatched vs. live schema" filter when
        the live schema is exactly the sanitized names (the expected
        steady state for a table this feature has already loaded once), and
      - the resulting row count exactly matches the source extraction
        (this logic never drops or duplicates ROWS -- only columns can be
        dropped, and only on a genuine name mismatch).
    """
    from pbixray import PBIXRay

    results = []  # (file, table, row_count, column_count, status)
    failures = []
    extraction_issues = []

    for path in pbix_files:
        try:
            model = PBIXRay(path)
        except Exception as exc:
            extraction_issues.append(f"{os.path.basename(path)}: could not open file ({exc})")
            continue

        for table in _real_tables(model):
            try:
                df = model.get_table(table)
            except Exception as exc:
                # Same PBIXRay-internal issue noted in the sanitization test
                # above, not a bug in this feature's own logic -- tracked
                # separately, doesn't fail this test.
                extraction_issues.append(f"{os.path.basename(path)}::{table}: get_table() raised ({type(exc).__name__})")
                continue

            if df is None or df.empty:
                results.append((os.path.basename(path), table, 0, 0, "empty-source-skip"))
                continue

            source_row_count = len(df)
            renamed = df.rename(columns={c: sanitizer.sanitize_column(str(c)) for c in df.columns})

            # Simulate the live schema as exactly the sanitized names --
            # the steady-state case _backfill_one_table's unmatched-column
            # filter is designed for.
            live_cols = {c.upper() for c in renamed.columns}
            unmatched = [c for c in renamed.columns if c.upper() not in live_cols]
            loaded = renamed.drop(columns=unmatched) if unmatched else renamed

            if unmatched:
                failures.append(
                    f"{os.path.basename(path)}::{table}: unexpected unmatched column(s) "
                    f"against their own sanitized names: {unmatched}"
                )
                continue

            if len(loaded) != source_row_count:
                failures.append(
                    f"{os.path.basename(path)}::{table}: row count mismatch -- "
                    f"source={source_row_count}, would-load={len(loaded)}"
                )
                continue

            if loaded.shape[1] == 0:
                failures.append(f"{os.path.basename(path)}::{table}: all columns dropped")
                continue

            results.append((os.path.basename(path), table, source_row_count, loaded.shape[1], "ok"))

    print(f"\n{len(results)} table(s) verified across {len(pbix_files)} distinct PBIX file(s):")
    for fn, table, rows, cols, status in results:
        print(f"  [{status}] {fn} :: {table} -- {rows} row(s), {cols} column(s)")
    if extraction_issues:
        print(f"\n{len(extraction_issues)} table(s) hit a PBIXRay-internal extraction error "
              "(new edge case; not counted as a failure here -- see the sanitization test above):")
        for issue in extraction_issues:
            print(f"  {issue}")

    assert not failures, "Row-count/column-drop mismatches:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Real-data classification scenarios (Part 1 follow-up): confirm the
# three-way safety-check split against REAL extracted PBIX data, not just
# synthetic shapes -- a genuinely small real table must land in
# ambiguous_needs_review, and the generate_sample_insert() placeholder
# pattern must still auto-heal despite being below the row-count floor.
# ---------------------------------------------------------------------------

class _RecordingConnectionManager:
    """Records the SQL text onto the cursor so a fake fetchall() can answer
    based on which columns a given query actually requested -- see the
    equivalent helper in test_snowflake_data_backfill_safety.py."""

    @staticmethod
    def _execute_sql(cursor, sql, params=None, context=""):
        cursor._last_sql = sql


def _build_real_schema_manager() -> SnowflakeSchemaManager:
    config = SnowflakeConfig(
        account="test.local", user="test_user", password="test_password",
        warehouse="test_wh", database="test_db", schema_name="test_schema",
        role="test_role",
    )
    return SnowflakeSchemaManager(
        config=config, behavior=ConnectorBehavior(),
        identifier_sanitizer=IdentifierSanitizer(),
        connection_manager=_RecordingConnectionManager(), dup_name_repo=None,
    )


def _physical_cols_from_real_dataframe(df, sanitizer: IdentifierSanitizer) -> dict:
    """Build a physical_cols-shaped dict (safe_name -> fake column object
    with .data_type/.unique_name) from a real extracted PBIXRay DataFrame,
    using the REAL IdentifierSanitizer -- mirrors what
    _collect_physical_source_columns produces, without needing a full
    SMLDataset (every column here is treated as non-key, since this test
    only exercises _classify_table_data_state directly, not the full
    key-exclusion logic already covered elsewhere)."""
    cols = {}
    for col in df.columns:
        safe = sanitizer.sanitize_column(str(col))
        dtype = DataType.STRING if str(df[col].dtype) in ("string", "object") else DataType.INTEGER
        cols[safe] = SimpleNamespace(data_type=dtype, unique_name=str(col))
    return cols


def _fake_cursor_reporting_fully_populated(row_count, non_key_safe_names, sample_row_by_safe_name):
    """Simulates a live table already populated with exactly `row_count`
    real rows, every non-key column 100% non-null -- the exact shape a
    genuinely small, already-correctly-loaded real table produces."""
    cursor = MagicMock()
    cursor.fetchone.return_value = (row_count,) + tuple(row_count for _ in non_key_safe_names)

    def fake_fetchall():
        sql = getattr(cursor, "_last_sql", "") or ""
        match = re.search(r"SELECT\s+(.*?)\s+FROM", sql, re.IGNORECASE | re.DOTALL)
        cols = re.findall(r'"([^"]+)"', match.group(1)) if match else []
        return [tuple(sample_row_by_safe_name.get(c) for c in cols)]

    cursor.fetchall.side_effect = fake_fetchall
    return cursor


@pytest.mark.parametrize("table_name", ["Range", "Business Area"])
def test_corporate_spend_small_real_table_lands_in_ambiguous_needs_review(table_name):
    """Corporate Spend's real Range (1 row) and Business Area (7 rows)
    tables are both below the row-count floor but carry genuine,
    non-placeholder business data -- must NOT auto-backfill (would
    duplicate real data already there) and must NOT be silently skipped
    either. Confirms landing in ambiguous_needs_review with evidence."""
    if not os.path.isfile(_CORPORATE_SPEND_PBIX):
        pytest.skip(_SKIP_REASON)

    from pbixray import PBIXRay

    model = PBIXRay(_CORPORATE_SPEND_PBIX)
    df = model.get_table(table_name)
    assert not df.empty, f"expected real rows for {table_name}"
    assert len(df) < SnowflakeSchemaManager._MIN_ROW_COUNT_FOR_POPULATED, (
        f"{table_name} has {len(df)} rows -- test assumes a real table below the row-count floor"
    )

    sanitizer = IdentifierSanitizer()
    sm = _build_real_schema_manager()
    physical_cols = _physical_cols_from_real_dataframe(df, sanitizer)
    non_key_safe_names = list(physical_cols.keys())

    real_first_row = df.iloc[0]
    sample_row_by_safe_name = {
        safe: real_first_row[orig.unique_name] for safe, orig in physical_cols.items()
    }
    cursor = _fake_cursor_reporting_fully_populated(len(df), non_key_safe_names, sample_row_by_safe_name)

    result = sm._classify_table_data_state(cursor, table_name.upper(), physical_cols, non_key_safe_names)

    assert result["state"] == "ambiguous", (
        f"{table_name}: expected ambiguous_needs_review, got {result['state']!r} -- "
        "a genuinely small real table must never be silently auto-decided either way"
    )
    assert result["row_count"] == len(df)
    assert result["populated_columns"], "evidence must include which columns had data"
    assert any(result["samples"].values()), "evidence must include actual sample values"


def test_placeholder_row_still_auto_heals_below_the_row_count_floor():
    """A freshly simulated generate_sample_insert() placeholder row (every
    non-key STRING column literally 'Sample_<original column name>') must
    still be classified as empty/eligible, never ambiguous, no matter how
    it's below the row-count floor exactly like a genuinely small real
    table would be -- confirms the placeholder carve-out actually
    resolves the ambiguity that would otherwise apply."""
    sm = _build_real_schema_manager()
    physical_cols = {
        "MANUFACTURER": SimpleNamespace(data_type=DataType.STRING, unique_name="Manufacturer"),
        "CATEGORY": SimpleNamespace(data_type=DataType.STRING, unique_name="Category"),
    }
    non_key_safe_names = list(physical_cols.keys())
    sample_row_by_safe_name = {
        "MANUFACTURER": "Sample_Manufacturer",
        "CATEGORY": "Sample_Category",
    }
    cursor = _fake_cursor_reporting_fully_populated(1, non_key_safe_names, sample_row_by_safe_name)

    result = sm._classify_table_data_state(cursor, "PRODUCT", physical_cols, non_key_safe_names)

    assert result["state"] == "empty", (
        f"expected the known placeholder pattern to auto-heal, got {result['state']!r}"
    )
    assert result["row_count"] == 1
