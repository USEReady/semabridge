"""Regression test: MetricsClauseBuilder must route a genuinely ambiguous
duplicate-sibling column reference (schema_manager's
AmbiguousColumnReferenceError) through the SAME drop_ledger "record a clear
reason and skip this one entity" pattern dimensions_clause_builder.py
already uses (see Tests/test_dimensions_clause_builder_ambiguous_column_handling.py)
-- never let it propagate as an uncaught exception and fail the whole
METRICS clause (and, transitively, the whole dry-run/deploy) over one
unresolvable direct-aggregation metric.

This call site (the SUM/COUNT/etc.-over-a-bare-column path, no DAX
expression) was previously unreachable for this exception at all, because
schema_manager._resolve_physical_column_name only ever raised it when live
schema was unconfirmed. Enabling duplicate-sibling resolution for the
live-confirmed case too (the SALES.UNIT / "invalid identifier" fix) makes
it reachable here in the far more common real-deploy scenario, so this
call site needs the same safety net dimensions_clause_builder.py already
has, not a bare unguarded call.

All identifiers below are synthetic placeholders, not tied to any real
model's column names.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.core.exceptions import AmbiguousColumnReferenceError
from semabridge.utils.identifiers import IdentifierSanitizer


class _DummySanitizer:
    def format_physical_column_ref(self, alias: str, col: str, model_name=None):
        return f'{alias}."{col}"'


class _AmbiguousOnOneColumnSchema:
    """Fake schema_manager: raises AmbiguousColumnReferenceError for one
    specific raw column reference (mirroring a genuine same-cased duplicate
    that couldn't be told apart), resolves normally for everything else --
    proving the ambiguity is isolated to just the one offending metric, not
    the whole METRICS build."""

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


def _builder(drop_ledger):
    return MetricsClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=_AmbiguousOnOneColumnSchema(),
        sanitizer=_DummySanitizer(),
        translator=MetricExpressionTranslator(identifier_sanitizer=IdentifierSanitizer()),
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
        drop_ledger=drop_ledger,
    )


def _direct_agg_metric(unique_name: str, dataset: str, source_column: str):
    return SimpleNamespace(
        unique_name=unique_name,
        dataset=dataset,
        expression=None,
        sql_expression=None,
        source_column=source_column,
        aggregation=SimpleNamespace(value="sum"),
        sync_enabled=True,
        sync_failure_reason=None,
        advisory_notes=[],
        advisory_categories=[],
    )


def test_ambiguous_direct_aggregation_metric_is_dropped_cleanly_not_an_uncaught_exception():
    ledger = DropLedger()
    builder = _builder(ledger)

    dataset = SimpleNamespace(unique_name="gizmo_fact", is_fact=True)
    model = SimpleNamespace(
        metrics=[
            _direct_agg_metric("TotalAmount", "gizmo_fact", "Amount"),
            _direct_agg_metric("TotalQuantity", "gizmo_fact", "Quantity"),
        ],
        unique_name="model",
        label="model",
    )

    # Must not raise -- the whole point of this fix.
    lines = builder.build_for_sml(
        model,
        dataset_aliases={"gizmo_fact": "GIZMO_FACT"},
        dataset_by_name={"gizmo_fact": dataset},
        dataset_col_lookup={"gizmo_fact": {"AMOUNT_1", "AMOUNT_2", "QUANTITY"}},
        alias_by_raw={},
        all_physical_col_names={"AMOUNT_1", "AMOUNT_2", "QUANTITY"},
        emittable_metric_name_set={"TOTALAMOUNT", "TOTALQUANTITY"},
    )

    joined = "\n".join(lines)
    # The non-ambiguous sibling metric still emits fine -- one bad
    # reference must never take down the rest of the METRICS clause.
    assert "TOTALQUANTITY" in joined
    assert 'GIZMO_FACT."QUANTITY"' in joined
    assert "TOTALAMOUNT" not in joined

    # The ambiguous one is cleanly recorded as a drop, with a clear reason,
    # not silently swallowed and not left unexplained.
    ambiguous_records = [r for r in ledger.records if r.entity_name == "TotalAmount"]
    assert len(ambiguous_records) == 1
    record = ambiguous_records[0]
    assert record.entity_kind == "metric"
    assert record.dataset == "gizmo_fact"
    assert record.stage == DropStage.DDL_EMISSION
    assert "ambiguous" in record.reason.lower()
    assert "AMOUNT_1" in record.reason and "AMOUNT_2" in record.reason
