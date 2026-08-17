"""Regression tests for extract_model_entities()'s physical-column
metadata-deduplication phase.

Before this fix, two physical columns that normalized to the same target
identifier were ALWAYS collapsed into one entity -- discarding the other
entirely -- based purely on comparing their SANITIZED identifier strings.
There was no check of any kind against the columns' actual source identity
(source_expression/data_type/source_column/...), so two genuinely DIFFERENT
columns that merely collided after sanitization (not true duplicates) could
silently lose one of them, indistinguishable in the code from the real,
intended case: the same real column reported twice by the source connector
under different casing.

The fix adds `_column_identity_signature()`: only collapse two colliding
columns when their signatures are both present and equal (proven identical
metadata); otherwise disambiguate with a suffix and keep BOTH, matching the
proven collision-handling pattern already used for dimension-alias and
metric-name collisions (snowflake_emitter_parts/identifier_utilities.py's
resolve_unique_dimension_alias / resolve_unique_metric_alias).

All identifiers below are synthetic placeholders, not tied to any real
model's column names.
"""
import logging

from semabridge.api.services.project_mapping_engine import extract_model_entities
from semabridge.core.drop_ledger import DropLedger, DropStage


def _physical_column(unique_name, data_type=None, source_expression=None, source_column=None):
    col = {"unique_name": unique_name, "type": "data"}
    if data_type is not None:
        col["data_type"] = data_type
    if source_expression is not None:
        col["source_expression"] = source_expression
    if source_column is not None:
        col["source_column"] = source_column
    return col


def _model_with_columns(columns, dataset_name="WIDGETSOURCE"):
    return {
        "unique_name": "synthetic_model",
        "datasets": [
            {"unique_name": dataset_name, "source_table": dataset_name, "columns": columns},
        ],
        "metrics": [],
    }


def _column_entities(entities):
    return [e for e in entities if e.get("entity_kind") == "column"]


def test_two_distinct_columns_colliding_after_sanitization_are_both_preserved_and_disambiguated(caplog):
    """Two genuinely different columns (different data_type, different
    source_column -- no shared identity signal) that merely collide on
    their sanitized target identifier must BOTH survive as separate
    entities, with the second one carrying a distinct, suffixed identifier
    -- never silently discarded."""
    columns = [
        _physical_column("widgetcode", data_type="STRING", source_column="widgetcode_src_a"),
        _physical_column("WidgetCode", data_type="NUMBER", source_column="widgetcode_src_b"),
    ]
    model = _model_with_columns(columns)
    ledger = DropLedger()

    with caplog.at_level(logging.WARNING, logger="semabridge.api.services.project_mapping_engine"):
        entities = extract_model_entities(model, target_connector="snowflake", drop_ledger=ledger)

    cols = _column_entities(entities)
    assert len(cols) == 2, "both distinct colliding columns must be preserved, not collapsed to one"

    source_names = {c["source_name"] for c in cols}
    assert source_names == {"widgetcode", "WidgetCode"}, "each entity must keep its own real source name unchanged"

    disambiguated = [c for c in cols if c.get("disambiguated_target_identifier")]
    assert len(disambiguated) == 1, "exactly one of the two colliding columns must have been suffixed"
    assert disambiguated[0]["disambiguated_target_identifier"] == "WIDGETCODE_2"

    # Nothing was dropped -- both entities are real, so the ledger must be empty.
    assert ledger.records == [], "disambiguation must never record a drop -- nothing was excluded"

    # Matches the proven collision-handling log style already used for
    # dimension-alias/metric-name collisions (identifier_utilities.py).
    warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "Physical column collision for 'WidgetCode' (base 'WIDGETCODE'); using 'WIDGETCODE_2'" in m
        for m in warning_messages
    ), f"expected a collision-style warning log, got: {warning_messages}"


def test_three_way_collision_assigns_distinct_suffixes_to_every_sibling():
    """A chain of 3+ genuinely distinct columns colliding on the same base
    must each get a distinct identifier -- never two entries silently
    sharing one suffix."""
    columns = [
        _physical_column("gizmoref", data_type="STRING", source_column="src_a"),
        _physical_column("GizmoRef", data_type="NUMBER", source_column="src_b"),
        _physical_column("GIZMOREF", data_type="BOOLEAN", source_column="src_c"),
    ]
    model = _model_with_columns(columns)

    entities = extract_model_entities(model, target_connector="snowflake")
    cols = _column_entities(entities)

    assert len(cols) == 3, "all three genuinely distinct columns must be preserved"
    resolved_identifiers = set()
    for c in cols:
        resolved_identifiers.add(c.get("disambiguated_target_identifier") or "GIZMOREF")
    assert len(resolved_identifiers) == 3, (
        f"every sibling must resolve to a distinct identifier, got: {resolved_identifiers}"
    )


def test_two_identical_duplicate_entries_are_still_collapsed_into_one_no_regression():
    """Two entries that are the SAME real column reported twice under
    different casing (identical source_expression/data_type/source_column)
    must still collapse to a single entity -- this is the one genuinely
    correct case for collapsing, and must not regress."""
    columns = [
        _physical_column(
            "widgetcode", data_type="STRING",
            source_expression="shared_src", source_column="shared_src",
        ),
        _physical_column(
            "WIDGETCODE", data_type="STRING",
            source_expression="shared_src", source_column="shared_src",
        ),
    ]
    model = _model_with_columns(columns)
    ledger = DropLedger()

    entities = extract_model_entities(model, target_connector="snowflake", drop_ledger=ledger)
    cols = _column_entities(entities)

    # Which specific one of the two equally-valid casings survives is
    # pre-existing tiebreak behavior this fix does not change (and does not
    # re-verify here) -- what matters for this regression check is that the
    # PROVEN-identical pair still collapses to exactly one entity, not zero
    # and not two.
    assert len(cols) == 1, "a proven identical duplicate must still collapse to one entity"
    assert cols[0]["source_name"] in ("widgetcode", "WIDGETCODE")
    assert not cols[0].get("disambiguated_target_identifier"), "a true duplicate collapse is not a disambiguation"

    # Exact original message text must survive unchanged -- this is the
    # literal string the reported "duplicate physical-column metadata"
    # symptom quoted, and must not regress.
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.stage == DropStage.EXTRACTION
    assert record.reason == (
        "Duplicate physical-column metadata for target identifier "
        "'WIDGETCODE' — a differently-cased duplicate was kept instead."
    )


def test_no_identity_signal_on_either_side_disambiguates_rather_than_collapses():
    """When neither colliding column carries ANY identity signal (blank
    data_type/source_expression/source_column on both), the signature
    comparison is indeterminate -- this must disambiguate (fail toward
    preserving data), never collapse on an unproven assumption."""
    columns = [
        {"unique_name": "sprocketid", "type": "data"},
        {"unique_name": "SprocketId", "type": "data"},
    ]
    model = _model_with_columns(columns)

    entities = extract_model_entities(model, target_connector="snowflake")
    cols = _column_entities(entities)

    assert len(cols) == 2, "an indeterminate identity comparison must never collapse two entries"
