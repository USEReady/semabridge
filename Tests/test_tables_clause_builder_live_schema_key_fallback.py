"""Regression test for a real incident: a dataset's live-schema columns
were silently invisible to dimensions_clause_builder.py's "only emit a
live-schema-only column (MONTHINDEX/YEARINDEX/QUARTERINDEX/WEEKINDEX) when
confirmed live" check, on every real deploy, even though the live schema
WAS fetched and DID contain the column (confirmed directly against a real
customer's Snowflake account via information_schema.columns).

Root cause: tables_clause_builder.py's _build() looked up
self.live_schema_metadata using exactly ONE key form
(sanitize_table_name(...).upper()) with no fallback -- if the live schema
dict's own keys happened to be in a different form (bare sanitized name,
or the raw/unsanitized source name), the lookup missed entirely and
silently fell through to "no live schema available", even though the data
was right there under a different key. The sibling method in this same
file, _build_source_query_with_anchors, already had this exact fallback
(bare name, upper, raw name, raw upper) -- _build() just never got it.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.alias_registry import AliasRegistry
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.utils.identifiers import IdentifierSanitizer


def _builder(live_schema_metadata):
    return TablesClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=None,
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
        # auto_add_anchors=False: this test is about live_col_lookup/
        # dataset_col_lookup population, not the (unrelated) fiscal-anchor
        # injection machinery -- keep the fixture minimal and focused.
        behavior=SimpleNamespace(snowflake=SimpleNamespace(pk_resolution_mode=None, auto_add_anchors=False)),
        live_schema_metadata=live_schema_metadata,
    )


def _dataset(unique_name, source_table, columns):
    return SimpleNamespace(
        unique_name=unique_name,
        source_table=source_table,
        is_fact=False,
        columns=[SimpleNamespace(unique_name=c, is_key=(c == columns[0])) for c in columns],
    )


def _model(datasets):
    return SimpleNamespace(datasets=datasets, relationships=[])


def test_live_schema_found_when_metadata_key_is_the_bare_sanitized_name():
    """The real incident's exact shape: live_schema_metadata keyed by the
    plain, non-uppercased sanitized table name -- the single-shot
    uppercase-only lookup used to miss this entirely."""
    date_ds = _dataset("Date", "Date", ["COL_DATE", "MONTHINDEX", "YEAR"])
    live_schema_metadata = {"Date": {"COL_DATE", "MONTHINDEX", "YEAR"}}
    builder = _builder(live_schema_metadata)

    _, _, _, dataset_col_lookup, _, live_col_lookup = builder.build_for_osi(
        _model([date_ds]), registry=AliasRegistry(), metric_counts_by_dataset={}, related_datasets=set(),
    )

    assert "Date" in live_col_lookup, "live schema should have been found under the bare-name key"
    assert live_col_lookup["Date"] == {"COL_DATE", "MONTHINDEX", "YEAR"}
    assert dataset_col_lookup["Date"] == {"COL_DATE", "MONTHINDEX", "YEAR"}


def test_live_schema_found_when_metadata_key_is_uppercase():
    """The pre-existing, already-working case -- must remain unaffected."""
    date_ds = _dataset("Date", "Date", ["COL_DATE", "MONTHINDEX"])
    live_schema_metadata = {"DATE": {"COL_DATE", "MONTHINDEX"}}
    builder = _builder(live_schema_metadata)

    _, _, _, dataset_col_lookup, _, live_col_lookup = builder.build_for_osi(
        _model([date_ds]), registry=AliasRegistry(), metric_counts_by_dataset={}, related_datasets=set(),
    )

    assert live_col_lookup["Date"] == {"COL_DATE", "MONTHINDEX"}


def test_live_schema_found_when_metadata_key_is_raw_unsanitized_source_name():
    """A source table name with characters sanitize_table_name would
    normally change (e.g. a space) -- if live_schema_metadata was built
    from the RAW name instead of the sanitized one, the lookup must still
    find it."""
    fact_ds = _dataset("Sales Fact", "Sales Fact", ["UNITS"])
    live_schema_metadata = {"Sales Fact": {"UNITS"}}
    builder = _builder(live_schema_metadata)

    _, _, _, dataset_col_lookup, _, live_col_lookup = builder.build_for_osi(
        _model([fact_ds]), registry=AliasRegistry(), metric_counts_by_dataset={}, related_datasets=set(),
    )

    assert "Sales Fact" in live_col_lookup
    assert live_col_lookup["Sales Fact"] == {"UNITS"}


def test_no_live_schema_at_all_falls_back_to_modeled_columns_as_before():
    """Negative control: when the live schema genuinely doesn't have this
    table under ANY key form, behavior must be unchanged -- fall back to
    modeled columns, and live_col_lookup gets no entry for it."""
    date_ds = _dataset("Date", "Date", ["COL_DATE", "MONTHINDEX"])
    builder = _builder(live_schema_metadata={})

    _, _, _, dataset_col_lookup, _, live_col_lookup = builder.build_for_osi(
        _model([date_ds]), registry=AliasRegistry(), metric_counts_by_dataset={}, related_datasets=set(),
    )

    assert "Date" not in live_col_lookup
    assert dataset_col_lookup["Date"] == {"COL_DATE", "MONTHINDEX"}  # modeled fallback
