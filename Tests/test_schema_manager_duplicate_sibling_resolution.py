"""Regression tests for _resolve_physical_column_name's handling of
duplicate-named physical columns.

_collect_physical_source_columns already disambiguates two physical columns
that collide after sanitization with a numeric suffix (e.g. "WidgetCode" and
"WIDGETCODE" both normalize to "WIDGETCODE", so it keeps both and resolves
them to "WIDGETCODE_1"/"WIDGETCODE_2" -- see that method's own docstring).
But a raw/un-suffixed column reference (e.g. a dimension attribute's
dataset_column, exactly as authored in the source model) can never appear as
a key in that suffixed set, so _resolve_physical_column_name's exact/
case-insensitive/date-pattern/fuzzy matching steps all missed it and fell
through to a naive re-sanitized default -- producing a physical name that
was never actually a key in the known-columns lookup, and downstream
schema validation (dimensions_clause_builder.py) reported it as "not found
in the known physical schema" for a column that genuinely existed, just
under a suffixed name.

The fix resolves such a reference by matching the ORIGINAL column object it
identifies (exact unique_name match, then case-insensitive), returning
whichever suffixed sibling that object actually resolved to -- and raises
AmbiguousColumnReferenceError, rather than guessing, when two or more
distinct columns genuinely can't be told apart from the reference alone.

All identifiers below are synthetic placeholders, not tied to any real
model's column names.
"""
import pytest

from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.exceptions import AmbiguousColumnReferenceError
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.sml.models import SMLDataset, SMLColumn


def _build_schema_manager() -> SnowflakeSchemaManager:
    config = SnowflakeConfig(
        account="test.local", user="u", password="p", warehouse="w",
        database="test_db", schema_name="test_schema", role="r",
    )
    behavior = ConnectorBehavior()
    idsan = IdentifierSanitizer(
        force_uppercase=behavior.compatibility.force_uppercase,
        always_quote=behavior.snowflake.quote_identifiers,
        suppress_reserved=behavior.compatibility.suppress_reserved_words,
    )
    return SnowflakeSchemaManager(
        config=config, behavior=behavior, identifier_sanitizer=idsan, connection_manager=None,
    )


def test_bare_reference_resolves_to_the_correct_suffixed_sibling():
    """Two genuinely distinct columns ("WidgetCode" / "WIDGETCODE") collide
    after sanitization and get disambiguated into WIDGETCODE_1/WIDGETCODE_2.
    A raw reference matching one of them EXACTLY must resolve to that
    specific sibling, not the other one, and not a bare unsuffixed guess."""
    sm = _build_schema_manager()
    dataset = SMLDataset(
        unique_name="WidgetSource",
        source_table="WidgetSource",
        columns=[
            SMLColumn(unique_name="WidgetCode", data_type="string"),
            SMLColumn(unique_name="WIDGETCODE", data_type="decimal"),
        ],
    )
    # No live schema for this dataset -- forces the modeled-fallback path
    # where _collect_physical_source_columns' suffixing actually matters.

    resolved_first = sm._resolve_physical_column_name(dataset, "WidgetCode")
    resolved_second = sm._resolve_physical_column_name(dataset, "WIDGETCODE")

    assert resolved_first == "WIDGETCODE_1"
    assert resolved_second == "WIDGETCODE_2"
    assert resolved_first != resolved_second, "each raw reference must resolve to ITS OWN sibling"

    # Prove it no longer falls back to the old, broken behavior: a bare
    # sanitized guess that was never actually a key in the resolved set.
    live_cols = set(sm._collect_physical_source_columns(dataset).keys())
    assert "WIDGETCODE" not in live_cols, "sanity check: the unsuffixed base is genuinely not a real key"


def test_non_colliding_column_is_unaffected_by_the_fix():
    """A dataset with no name collisions at all must resolve exactly as
    before -- this fix only changes behavior for the duplicate-sibling case."""
    sm = _build_schema_manager()
    dataset = SMLDataset(
        unique_name="GadgetSource",
        source_table="GadgetSource",
        columns=[SMLColumn(unique_name="GadgetId", data_type="string")],
    )
    assert sm._resolve_physical_column_name(dataset, "GadgetId") == "GADGETID"


def test_genuinely_ambiguous_duplicate_fails_closed_instead_of_guessing():
    """Two columns sharing the EXACT SAME unique_name (a genuine same-cased
    duplicate, not merely a casing collision) are both disambiguated into
    siblings by _collect_physical_source_columns, but a raw reference of
    that exact name can't tell them apart -- this must fail closed with a
    clear, typed error, never silently pick one and risk emitting SQL
    against the wrong physical column's data."""
    sm = _build_schema_manager()
    dataset = SMLDataset(
        unique_name="GizmoSource",
        source_table="GizmoSource",
        columns=[
            SMLColumn(unique_name="Amount", data_type="string"),
            SMLColumn(unique_name="Amount", data_type="decimal"),
        ],
    )

    with pytest.raises(AmbiguousColumnReferenceError) as exc_info:
        sm._resolve_physical_column_name(dataset, "Amount")

    err = exc_info.value
    assert "Amount" in str(err)
    assert err.raw_col_name == "Amount"
    assert err.dataset_name == "GizmoSource"
    assert len(err.candidates) == 2, "the error must surface every candidate it couldn't choose between"


def test_ambiguous_case_insensitive_duplicate_also_fails_closed():
    """Two columns whose unique_name only matches the reference
    case-insensitively (not exactly), with neither an exact match, must also
    fail closed rather than guess between them."""
    sm = _build_schema_manager()
    dataset = SMLDataset(
        unique_name="SprocketSource",
        source_table="SprocketSource",
        columns=[
            SMLColumn(unique_name="sprocketid", data_type="string"),
            SMLColumn(unique_name="SPROCKETID", data_type="decimal"),
        ],
    )

    # Neither column's unique_name is an EXACT match for "SprocketId" (mixed
    # case), but both match case-insensitively -- genuinely ambiguous.
    with pytest.raises(AmbiguousColumnReferenceError):
        sm._resolve_physical_column_name(dataset, "SprocketId")


def test_live_schema_confirmed_bypasses_the_new_resolution_entirely():
    """When the dataset's own live Snowflake schema IS confirmed, resolution
    must go through the existing live-schema checks unchanged -- the new
    duplicate-sibling resolution only applies to the modeled-fallback path."""
    sm = _build_schema_manager()
    dataset = SMLDataset(
        unique_name="WidgetSource",
        source_table="WidgetSource",
        columns=[
            SMLColumn(unique_name="WidgetCode", data_type="string"),
            SMLColumn(unique_name="WIDGETCODE", data_type="decimal"),
        ],
    )
    # A live schema is confirmed and (deliberately) does NOT reflect the
    # modeled duplicate-suffix bookkeeping at all -- if the fix were wrongly
    # applied here too, it would try to consult _collect_physical_source_columns
    # instead of just trusting the live, confirmed column list.
    sm._live_schema_metadata["WIDGETSOURCE"] = {"WIDGETCODE"}

    resolved = sm._resolve_physical_column_name(dataset, "WidgetCode")
    assert resolved == "WIDGETCODE", "live-confirmed schema must resolve directly, bypassing duplicate-sibling logic"
