"""Regression tests for the synonym-lookup ambiguity guard.

lookup_attribute_synonyms (utils/synonyms.py) used to resolve a candidate
column name via dataset_obj.get_column()/a manual case-insensitive scan,
both first-match-wins with no way to detect or report ambiguity. When two
distinct columns collide by name (the same shape of collision
schema_manager._resolve_duplicate_sibling_physical_name already guards
against for physical-column resolution), this silently attached whichever
column's synonyms happened to be found first to EVERY attribute matching
that name -- the "duplicate synonyms across suffixed columns" symptom
(amount/amount_2, category/category_2/product_3, etc. all broadcasting the
same synonym set to Cortex Analyst).

Fix: raise AmbiguousColumnReferenceError instead of guessing. Unlike the
physical-column-resolution ambiguity (which must drop the entity -- there's
no safe SQL to emit), a synonym-lookup ambiguity is lower severity: the
attribute's own physical column was already resolved unambiguously before
synonym lookup ever runs, so the entity stays valid and queryable -- only
its synonym enrichment is skipped, with a warning logged, not dropped via
drop_ledger (nothing was actually excluded).

Covers all three layers: the raising function itself, the
dimensions_clause_builder.py DDL-generation caller, and the
snowflake_emitter_parts/renderers.py Cortex YAML side-car caller.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
from semabridge.connectors.snowflake_emitter_parts.renderers import _safe_lookup_attribute_synonyms
from semabridge.core.exceptions import AmbiguousColumnReferenceError
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.synonyms import lookup_attribute_synonyms


def _col(unique_name: str, synonyms):
    return SimpleNamespace(unique_name=unique_name, synonyms=synonyms)


def _dataset(unique_name: str, columns):
    return SimpleNamespace(unique_name=unique_name, columns=columns)


# ---------------------------------------------------------------------------
# 1. lookup_attribute_synonyms itself
# ---------------------------------------------------------------------------

def test_non_ambiguous_lookup_still_returns_the_right_synonyms():
    """Regression: the common, non-colliding case must be unaffected."""
    dataset = _dataset("Sales", [_col("Amount", ["revenue", "sales value"]), _col("Quantity", ["units"])])
    attr = SimpleNamespace(source_column="Amount", dataset_column=None, unique_name="Amount")

    assert lookup_attribute_synonyms(attr, dataset) == ["revenue", "sales value"]


def test_exact_name_collision_raises_ambiguous_instead_of_guessing():
    """Two DIFFERENT real columns (e.g. sales-context vs returns-context
    'Amount') that happen to share the exact same unique_name must never
    silently merge into one synonym set."""
    dataset = _dataset(
        "SalesReturns",
        [
            _col("Amount", ["sales amount", "gross sales"]),
            _col("Amount", ["return amount", "refund value"]),
        ],
    )
    attr = SimpleNamespace(source_column="Amount", dataset_column=None, unique_name="Amount")

    with pytest.raises(AmbiguousColumnReferenceError) as exc_info:
        lookup_attribute_synonyms(attr, dataset)

    err = exc_info.value
    assert err.dataset_name == "SalesReturns"
    assert err.raw_col_name == "Amount"
    assert len(err.candidates) == 2


def test_case_insensitive_collision_also_raises_ambiguous():
    # Neither column's unique_name is an EXACT match for "Unit" (mixed
    # case), but both match case-insensitively -- genuinely ambiguous.
    dataset = _dataset("Widgets", [_col("unit", ["single item"]), _col("UNIT", ["measurement unit"])])
    attr = SimpleNamespace(source_column="Unit", dataset_column=None, unique_name="Unit")

    with pytest.raises(AmbiguousColumnReferenceError):
        lookup_attribute_synonyms(attr, dataset)


def test_no_match_at_all_returns_empty_list_not_an_error():
    dataset = _dataset("Widgets", [_col("Quantity", ["count"])])
    attr = SimpleNamespace(source_column="NoSuchColumn", dataset_column=None, unique_name="NoSuchColumn")

    assert lookup_attribute_synonyms(attr, dataset) == []


# ---------------------------------------------------------------------------
# 2. dimensions_clause_builder.py: keep the entity, skip synonyms, log a note
# ---------------------------------------------------------------------------

class _DDL:
    def sanitize_semantic_name(self, name: str) -> str:
        return IdentifierSanitizer().sanitize_column(name)

    def format_physical_column_ref(self, alias: str, column: str, model_name: str | None = None) -> str:
        return f'{alias}."{column}"'


class _AlwaysResolvesSchema:
    def _resolve_physical_column_name(self, dataset, column: str) -> str:
        return IdentifierSanitizer().sanitize_column(column)


def _dimensions_builder():
    return DimensionsClauseBuilder(
        IdentifierSanitizer(),
        _AlwaysResolvesSchema(),
        _DDL(),
        translator=None,
        behavior=SimpleNamespace(semantic_model=SimpleNamespace(sync_all_attributes=True)),
    )


def test_dimension_attribute_with_ambiguous_synonyms_is_still_emitted(caplog):
    """The physical column resolves fine (no collision at that layer) --
    only the synonym LOOKUP is ambiguous, because the dataset happens to
    have two distinct columns sharing this attribute's exact name. The
    dimension must still be emitted, just without synonyms."""
    caplog.set_level(logging.WARNING)

    colliding_columns = [_col("Amount", ["sales amount"]), _col("Amount", ["return amount"])]
    dataset = _dataset("gizmo_fact", colliding_columns)
    dim = SimpleNamespace(
        attributes=[
            SimpleNamespace(dataset="gizmo_fact", unique_name="Amount", source_column="Amount", dataset_column="Amount"),
        ]
    )
    sml = SimpleNamespace(dimensions=[dim], datasets=[dataset], unique_name="model", label="model")

    builder = _dimensions_builder()
    lines, missing_dims = builder.build_for_sml(
        sml,
        dataset_aliases={"gizmo_fact": "GIZMO_FACT"},
        dataset_by_name={"gizmo_fact": dataset},
        dataset_col_lookup={"gizmo_fact": {"AMOUNT"}},
        measure_columns=set(),
    )

    # The attribute is still emitted -- not dropped.
    assert len(lines) == 1
    assert 'GIZMO_FACT."AMOUNT"' in lines[0]
    # No synonyms clause attached (would render as a SNOWFLAKE_SYNONYMS suffix).
    assert "WITH SYNONYMS" not in lines[0]
    assert missing_dims == {}

    assert any("ambiguous" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# 3. renderers.py's Cortex YAML side-car: same degrade-gracefully behavior
# ---------------------------------------------------------------------------

def test_cortex_yaml_synonym_lookup_degrades_gracefully_on_ambiguity(caplog):
    caplog.set_level(logging.WARNING)

    dataset = _dataset("gizmo_fact", [_col("Amount", ["sales amount"]), _col("Amount", ["return amount"])])
    attr = SimpleNamespace(dataset="gizmo_fact", unique_name="Amount", source_column="Amount", dataset_column="Amount")

    result = _safe_lookup_attribute_synonyms(attr, dataset, "Amount")

    assert result == []
    assert any("ambiguous" in rec.message.lower() for rec in caplog.records)


def test_cortex_yaml_synonym_lookup_unaffected_when_unambiguous():
    dataset = _dataset("gizmo_fact", [_col("Amount", ["sales amount"])])
    attr = SimpleNamespace(dataset="gizmo_fact", unique_name="Amount", source_column="Amount", dataset_column="Amount")

    assert _safe_lookup_attribute_synonyms(attr, dataset, "Amount") == ["sales amount"]
