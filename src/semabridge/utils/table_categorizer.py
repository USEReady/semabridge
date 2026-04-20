"""Deterministic table categorization for semantic routing."""

from __future__ import annotations

import re
from typing import Any

from semabridge.sml.models import SMLDataset, SMLMetric, SMLModel
from semabridge.utils.semantic_graph import SemanticGraph

CATEGORY_FACT = "FACT"
CATEGORY_DIMENSION = "DIMENSION"
CATEGORY_BRIDGE = "BRIDGE"

CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_LOW = "LOW"

_DAX_TABLE_REFERENCE_PATTERN = re.compile(
    r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*\[[^\]]+\]",
    re.IGNORECASE,
)


def _normalize_name(name: str) -> str:
    return str(name or "").strip().upper()


def _get_tables(model: SMLModel) -> list[SMLDataset]:
    if hasattr(model, "datasets"):
        return list(model.datasets)
    return list(getattr(model, "tables", []))


def _set_tables(model: SMLModel, tables: list[SMLDataset]) -> None:
    if hasattr(model, "datasets"):
        model.datasets = tables
        return
    setattr(model, "tables", tables)


def _get_measures(model: SMLModel) -> list[SMLMetric]:
    if hasattr(model, "metrics"):
        return list(model.metrics)
    return list(getattr(model, "measures", []))


def _ensure_review_required_measures(model: SMLModel) -> list[dict[str, Any]]:
    existing = getattr(model, "review_required_measures", None)
    if isinstance(existing, list):
        return existing

    # Use object.__setattr__ to avoid depending on a specific pydantic extra-fields config.
    object.__setattr__(model, "review_required_measures", [])
    review_required = getattr(model, "review_required_measures")
    if not isinstance(review_required, list):
        raise ValueError("Expected review_required_measures to be a list")
    return review_required


def _extract_referenced_table_names(expression: str) -> set[str]:
    referenced_tables: set[str] = set()
    for quoted_name, bare_name in _DAX_TABLE_REFERENCE_PATTERN.findall(expression or ""):
        candidate = str(quoted_name or bare_name or "").strip()
        if candidate:
            referenced_tables.add(_normalize_name(candidate))
    return referenced_tables


def route_orphaned_measures(measures: list[SMLMetric], sml_model: SMLModel) -> dict[str, int]:
    """Route measures extracted from dummy tables.

    Current behavior intentionally uses a conservative heuristic:
    - Route when exactly one physical table is referenced in expression.
    - Flag as review-required when references are ambiguous or absent.

    TODO: Replace regex heuristics with DAX lineage parsing and confidence scoring.
    """
    table_lookup: dict[str, SMLDataset] = {
        _normalize_name(table.unique_name): table
        for table in _get_tables(sml_model)
        if len(table.columns) > 0
    }
    review_required = _ensure_review_required_measures(sml_model)

    routed = 0
    flagged = 0

    for measure in measures:
        referenced = _extract_referenced_table_names(str(measure.expression or ""))
        candidates = [table_lookup[name] for name in referenced if name in table_lookup]

        if len(candidates) == 1:
            measure.dataset = candidates[0].unique_name
            routed += 1
            continue

        measure.sync_enabled = False
        measure.sync_failure_reason = "DUMMY_TABLE_ROUTING_AMBIGUOUS"
        review_required.append(
            {
                "measure_name": measure.unique_name,
                "original_table": measure.dataset,
                "candidate_tables": sorted(table.unique_name for table in candidates),
                "reason": "Unable to infer a single fact table from expression references",
            }
        )
        flagged += 1

    return {
        "measures_routed": routed,
        "measures_flagged": flagged,
    }


def extract_and_remove_dummy_tables(sml_model: SMLModel) -> tuple[SMLModel, dict[str, int]]:
    """Extract measures from dummy tables and remove dummy/dead tables from the model.

    Dummy table: ``len(table.columns) == 0`` and table has at least one measure.
    Dead table: ``len(table.columns) == 0`` and table has no measures.
    """
    tables = _get_tables(sml_model)
    measures = _get_measures(sml_model)

    measures_by_table: dict[str, list[SMLMetric]] = {}
    for measure in measures:
        measures_by_table.setdefault(_normalize_name(measure.dataset), []).append(measure)

    kept_tables: list[SMLDataset] = []
    extracted_measures: list[SMLMetric] = []
    dropped_tables: set[str] = set()
    dummy_tables_dropped = 0
    dead_tables_dropped = 0

    for table in tables:
        table_key = _normalize_name(table.unique_name)
        table_measures = measures_by_table.get(table_key, [])

        if len(table.columns) > 0:
            kept_tables.append(table)
            continue

        if table_measures:
            extracted_measures.extend(table_measures)
            dropped_tables.add(table_key)
            dummy_tables_dropped += 1
            continue

        dropped_tables.add(table_key)
        dead_tables_dropped += 1

    _set_tables(sml_model, kept_tables)

    if hasattr(sml_model, "relationships"):
        sml_model.relationships = [
            relationship
            for relationship in sml_model.relationships
            if _normalize_name(relationship.from_dataset) not in dropped_tables
            and _normalize_name(relationship.to_dataset) not in dropped_tables
        ]

    route_summary = route_orphaned_measures(extracted_measures, sml_model)
    summary = {
        "dummy_tables_dropped": dummy_tables_dropped,
        "dead_tables_dropped": dead_tables_dropped,
        "measures_routed": route_summary["measures_routed"],
        "measures_flagged": route_summary["measures_flagged"],
    }
    return sml_model, summary


class TableCategorizer:
    """Classify semantic model tables into FACT/DIMENSION/BRIDGE roles."""

    def __init__(self) -> None:
        self._categories: dict[str, dict[str, str]] = {}

    def categorize_with_preprocessing(
        self,
        sml_model: SMLModel,
    ) -> tuple[SMLModel, dict[str, dict[str, str]], dict[str, int]]:
        """Preprocess model dummy tables before deterministic categorization."""
        mutated_model, summary = extract_and_remove_dummy_tables(sml_model)
        graph = SemanticGraph(mutated_model)
        categories = self.categorize(graph)
        return mutated_model, categories, summary

    def categorize(self, graph: SemanticGraph) -> dict[str, dict[str, str]]:
        """Categorize all tables in deterministic order.

        Returns:
            Mapping of table name -> {category, confidence, reason_code}
        """
        results: dict[str, dict[str, str]] = {}

        for table in graph.tables:
            outgoing = graph.get_neighbors(table)
            incoming = graph.get_incoming_neighbors(table)

            outgoing_many_count = sum(1 for card in outgoing.values() if card == "many_to_one")
            incoming_many_count = sum(1 for card in incoming.values() if card == "many_to_one")

            if graph.is_many_to_many_table(table):
                results[table] = {
                    "category": CATEGORY_BRIDGE,
                    "confidence": CONFIDENCE_MEDIUM,
                    "reason_code": "MANY_TO_MANY_PATH",
                }
                continue

            if outgoing_many_count > 0 and incoming_many_count > 0:
                results[table] = {
                    "category": CATEGORY_BRIDGE,
                    "confidence": CONFIDENCE_MEDIUM,
                    "reason_code": "MIXED_MANY_TO_ONE_DIRECTION",
                }
                continue

            if outgoing_many_count > 0:
                results[table] = {
                    "category": CATEGORY_FACT,
                    "confidence": CONFIDENCE_HIGH,
                    "reason_code": "OUTGOING_MANY_TO_ONE",
                }
                continue

            if incoming_many_count > 0:
                results[table] = {
                    "category": CATEGORY_DIMENSION,
                    "confidence": CONFIDENCE_HIGH,
                    "reason_code": "INCOMING_MANY_TO_ONE",
                }
                continue

            if outgoing or incoming:
                results[table] = {
                    "category": CATEGORY_BRIDGE,
                    "confidence": CONFIDENCE_LOW,
                    "reason_code": "NON_STANDARD_CARDINALITY",
                }
                continue

            # Single-table or isolated table defaults to FACT to keep deployment possible.
            # Dummy/dead table removal should run before categorization via
            # `categorize_with_preprocessing`.
            results[table] = {
                "category": CATEGORY_FACT,
                "confidence": CONFIDENCE_LOW,
                "reason_code": "ISOLATED_DEFAULT_FACT",
            }

        self._categories = results
        return dict(results)

    def get_category(self, table: str) -> tuple[str, str]:
        """Return (category, confidence) for a previously-categorized table."""
        record = self._categories.get(table)
        if not record:
            raise ValueError(f"Table '{table}' has not been categorized")
        return record["category"], record["confidence"]
