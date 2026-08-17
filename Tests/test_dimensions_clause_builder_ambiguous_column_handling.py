"""Regression test: DimensionsClauseBuilder must route a genuinely ambiguous
duplicate-sibling column reference (schema_manager's
AmbiguousColumnReferenceError -- see
Tests/test_schema_manager_duplicate_sibling_resolution.py for that
resolver's own tests) through the SAME drop_ledger "record a clear reason
and skip this one entity" pattern already used for every other
schema-validation miss -- never let it propagate as an uncaught exception
and fail the whole DIMENSIONS build (and, transitively, the whole
dry-run/deploy) over one unresolvable dimension.

All identifiers below are synthetic placeholders, not tied to any real
model's column names.
"""
from types import SimpleNamespace

from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.core.exceptions import AmbiguousColumnReferenceError
from semabridge.utils.identifiers import IdentifierSanitizer


class _DDL:
    def sanitize_semantic_name(self, name: str) -> str:
        return IdentifierSanitizer().sanitize_column(name)

    def format_physical_column_ref(self, alias: str, column: str, model_name: str | None = None) -> str:
        return f'{alias}."{column}"'


class _AmbiguousOnOneColumnSchema:
    """Fake schema_manager: raises AmbiguousColumnReferenceError for one
    specific raw column reference (mirroring a genuine same-cased duplicate
    that couldn't be told apart), resolves normally for everything else --
    proving the ambiguity is isolated to just the one offending dimension,
    not the whole DIMENSIONS build."""

    def _resolve_physical_column_name(self, dataset, column: str) -> str:
        if column == "Amount":
            raise AmbiguousColumnReferenceError(
                f"Column reference '{column}' matches 2 distinct duplicate-named "
                "physical columns and cannot be resolved unambiguously.",
                dataset_name=getattr(dataset, "unique_name", None),
                raw_col_name=column,
                candidates=["AMOUNT_1", "AMOUNT_2"],
            )
        return IdentifierSanitizer().sanitize_column(column)


def _build_builder(drop_ledger):
    return DimensionsClauseBuilder(
        IdentifierSanitizer(),
        _AmbiguousOnOneColumnSchema(),
        _DDL(),
        translator=None,
        behavior=SimpleNamespace(semantic_model=SimpleNamespace(sync_all_attributes=True)),
        drop_ledger=drop_ledger,
    )


def test_ambiguous_dimension_is_dropped_cleanly_not_an_uncaught_exception():
    ledger = DropLedger()
    builder = _build_builder(ledger)

    dataset = SimpleNamespace(unique_name="gizmo_fact", columns=[])
    dim = SimpleNamespace(
        attributes=[
            SimpleNamespace(
                dataset="gizmo_fact",
                unique_name="Amount",
                source_column="Amount",
                dataset_column="Amount",
            ),
            SimpleNamespace(
                dataset="gizmo_fact",
                unique_name="Quantity",
                source_column="Quantity",
                dataset_column="Quantity",
            ),
        ]
    )
    sml = SimpleNamespace(dimensions=[dim], datasets=[dataset], unique_name="model", label="model")

    # Must not raise -- the whole point of this fix.
    lines, missing_dims = builder.build_for_sml(
        sml,
        dataset_aliases={"gizmo_fact": "GIZMO_FACT"},
        dataset_by_name={"gizmo_fact": dataset},
        dataset_col_lookup={"gizmo_fact": {"AMOUNT_1", "AMOUNT_2", "QUANTITY"}},
        measure_columns=set(),
    )

    # The non-ambiguous sibling dimension still emits fine -- one bad
    # reference must never take down the rest of the DIMENSIONS clause.
    assert len(lines) == 1
    assert 'GIZMO_FACT."QUANTITY"' in lines[0]

    # The ambiguous one is cleanly recorded as a drop, with a clear reason,
    # not silently swallowed and not left unexplained.
    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.entity_kind == "column"
    assert record.entity_name == "Amount"
    assert record.dataset == "gizmo_fact"
    assert record.stage == DropStage.SCHEMA_VALIDATION
    assert "ambiguous" in record.reason.lower()
    assert "AMOUNT_1" in record.reason and "AMOUNT_2" in record.reason

    # Never added to missing_dims -- that structure feeds ALTER TABLE ADD
    # COLUMN, which must never run against a name we aren't even sure is
    # correct (see _safe_resolve_physical_column_name's docstring).
    assert missing_dims == {}


def test_ambiguous_raw_attribute_reference_is_also_dropped_cleanly():
    """Same coverage for call site #2 (the 'raw attributes' loop, columns
    with no explicit dimension defined)."""
    ledger = DropLedger()
    builder = _build_builder(ledger)

    amount_col = SimpleNamespace(unique_name="Amount", label="Amount", synonyms=[])
    quantity_col = SimpleNamespace(unique_name="Quantity", label="Quantity", synonyms=[])
    dataset = SimpleNamespace(unique_name="gizmo_fact", columns=[amount_col, quantity_col])
    sml = SimpleNamespace(dimensions=[], datasets=[dataset], unique_name="model", label="model")

    lines, missing_dims = builder.build_for_sml(
        sml,
        dataset_aliases={"gizmo_fact": "GIZMO_FACT"},
        dataset_by_name={"gizmo_fact": dataset},
        dataset_col_lookup={"gizmo_fact": {"AMOUNT_1", "AMOUNT_2", "QUANTITY"}},
        measure_columns=set(),
    )

    assert len(lines) == 1
    assert 'GIZMO_FACT."QUANTITY"' in lines[0]

    assert len(ledger.records) == 1
    record = ledger.records[0]
    assert record.entity_name == "Amount"
    assert record.stage == DropStage.SCHEMA_VALIDATION
    assert "ambiguous" in record.reason.lower()
    assert missing_dims == {}
