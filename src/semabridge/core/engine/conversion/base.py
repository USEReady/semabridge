from __future__ import annotations

import time
import uuid
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Literal, Optional
import yaml
from pydantic import Field
from semabridge.connectors.snowflake_emitter import MissingSourceTableWarning
from semabridge.core.settings import Settings, get_settings
from semabridge.core.config_loader import get_project_file_path
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.sync_modes import apply_sync_mode
from semabridge.core.run_summary import (
    STEP_NAMES,
    RunStatus,
    RunSummary,
    StepStatus,
    create_run_summary,
)
from semabridge.core.source_format import (
    SourceFormat,
    from_fabric_tmsl,
    from_pbix_tmsl,
    from_snowflake_metadata,
)
from semabridge.intermediate.models import OSIModel
from semabridge.sml.models import SMLModel, SMLRelationship
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import get_logger
from semabridge.utils.relationship_naming import generate_relationship_name
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.exceptions import (
    ConfigValidationError,
    AuthenticationError,
    ExtractionError,
    SourceFormatError,
    ConversionError,
    PersistenceError,
    DeploymentError,
)

logger = get_logger(__name__)

def _step5_validate_source(self, context: RunContext) -> None:
    """
    Step 5: Validate and parse source format.

    - Apply format definition validation
    - Parse using explicit parsing instructions
    - Fail with actionable diagnostics if invalid
    """
    self._current_step = 5
    logger.info("Step 5: Validating source format")

    if not context.source_format:
        self._record_step(5, StepStatus.FAILED, "No source format to validate")
        raise SourceFormatError("No source format artifact available")

    issues = context.source_format.validate_format()
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    if errors:
        msg = context.source_format.get_diagnostic_message()
        self._record_step(5, StepStatus.FAILED, f"{len(errors)} validation errors")
        raise SourceFormatError(msg)

    if warnings:
        self._record_step(5, StepStatus.SUCCESS, f"{len(warnings)} warnings")
    else:
        self._record_step(5, StepStatus.SUCCESS, "Source format valid")

def _step6_convert_to_sml(
    self,
    context: RunContext,
    workspace_id: Optional[str],
    dataset_id: Optional[str],
    force: bool = False,
) -> SMLModel:
    """
    Step 6: Convert to canonical SML.

    - Map validated Source Format into canonical SML
    - Ensure schema correctness and semantic consistency
    - Apply sync_mode logic (UPSERT: merge with target; COPY: use source only)
    """
    self._current_step = 6
    logger.info("Step 6: Converting to canonical SML")

    try:
        if context.source_type == "snowflake":
            sml_model = self._convert_snowflake_to_sml(context)
        elif context.source_type == "fabric":
            sml_model = self._convert_fabric_to_sml(context, workspace_id, dataset_id)
        elif context.source_type == "pbix":
            sml_model = self._convert_pbix_to_sml(context)
        else:
            raise ConversionError(f"Unknown source type: {context.source_type}")

        if sml_model is None:
            return None

        # Normalize relationship names/deduplication here so all downstream
        # target conversions and deployments operate on the same final model.
        self._normalize_relationships_for_target(sml_model)
        
        # Apply sync_mode logic after source conversion so target-only Snowflake
        # objects are preserved before target-format generation/deployment.
        sync_mode = str(getattr(context, 'sync_mode', 'copy') or 'copy').strip().lower()
        target_model = getattr(context, 'target_sml_model', None)

        if sync_mode == "copy":
            logger.info(f"COPY mode: Using source only (target-only entities will be deleted)")
        elif sync_mode == "upsert":
            logger.info("UPSERT mode: fetching live target state")
            if target_model is not None:
                logger.info("Live target fetched, applying merge")
                logger.info(
                    "UPSERT source before merge: %d datasets, %d metrics, %d dimensions",
                    len(sml_model.datasets),
                    len(sml_model.metrics),
                    len(sml_model.dimensions),
                )
                logger.info(
                    "UPSERT target before merge: %d datasets, %d metrics, %d dimensions",
                    len(target_model.datasets),
                    len(target_model.metrics),
                    len(target_model.dimensions),
                )
                sml_model = apply_sync_mode(sml_model, target_model, "upsert")
                logger.info(
                    "Merge complete: %d datasets, %d metrics, %d dimensions",
                    len(sml_model.datasets),
                    len(sml_model.metrics),
                    len(sml_model.dimensions),
                )
            else:
                logger.warning("Could not fetch live target, falling back to COPY behavior")
        elif target_model is None:
            logger.info(f"No existing target found - treating as new deployment (source-only behavior)")
        
        return sml_model

    except Exception as e:
        self._record_step(6, StepStatus.FAILED, str(e))
        raise ConversionError(f"SML conversion failed: {e}") from e

def _normalize_relationships_for_target(self, model: SMLModel) -> None:
    """Canonicalize and deduplicate relationships on the final SML model.

    Stage placement is intentional: after canonical SML creation and before
    target-format conversion/deployment.

    Deduplication strategy:
    1. Remove exact endpoint duplicates (same columns)
    2. Remove multiple relationships between same table pair (keep strongest)
    3. Remove direct relationships that create cycles (redundant paths)
       - If A->B exists and A->C->B exists, remove A->B (shorter path preferred)
       - Fabric's ambiguous path error indicates we need to break cycles
    """
    if not getattr(model, "relationships", None):
        return

    normalized: list[SMLRelationship] = []
    seen_endpoints: set[tuple[str, str, str, str]] = set()
    table_pair_candidates: dict[tuple[str, str], list[SMLRelationship]] = {}
    renamed_count = 0
    deduped_count = 0

    # First pass: collect by exact endpoint and table pair
    for rel in model.relationships:
        from_col = rel.from_columns[0] if rel.from_columns else ""
        to_col = rel.to_columns[0] if rel.to_columns else ""
        endpoint_key = (
            rel.from_dataset.upper(),
            from_col.upper(),
            rel.to_dataset.upper(),
            to_col.upper(),
        )

        # Skip exact endpoint duplicates
        if endpoint_key in seen_endpoints:
            deduped_count += 1
            continue
        seen_endpoints.add(endpoint_key)

        # Group by table pair for ambiguity detection
        table_pair = (rel.from_dataset.upper(), rel.to_dataset.upper())
        if table_pair not in table_pair_candidates:
            table_pair_candidates[table_pair] = []
        table_pair_candidates[table_pair].append(rel)

    # Second pass: resolve ambiguous table pairs (keep strongest relationship)
    candidate_rels: list[SMLRelationship] = []
    for table_pair, rel_group in table_pair_candidates.items():
        if len(rel_group) > 1:
            # Multiple relationships between same tables - keep strongest
            best_rel = self._select_strongest_relationship(rel_group)
            logger.info(
                "Ambiguous relationships detected for %s -> %s: "
                "keeping %s (others removed)",
                table_pair[0],
                table_pair[1],
                best_rel.unique_name,
            )
            deduped_count += len(rel_group) - 1
            candidate_rels.append(best_rel)
        else:
            candidate_rels.append(rel_group[0])

    # Third pass: detect and break cycles/transitive ambiguities
    # Build a graph to detect if keeping A->B creates redundant paths
    graph: dict[str, set[str]] = {}
    for rel in candidate_rels:
        from_table = rel.from_dataset.upper()
        to_table = rel.to_dataset.upper()
        if from_table not in graph:
            graph[from_table] = set()
        graph[from_table].add(to_table)

    # Check each relationship to see if removing it eliminates cycles
    final_rels: list[SMLRelationship] = []
    for rel in candidate_rels:
        from_table = rel.from_dataset.upper()
        to_table = rel.to_dataset.upper()

        # Check if there's an alternate path (excluding this direct edge)
        has_alternate_path = self._has_path(from_table, to_table, graph, exclude_edge=(from_table, to_table))

        if has_alternate_path:
            logger.info(
                "Cycle detected for %s -> %s: removing direct edge (alternate path exists via other tables)",
                from_table,
                to_table,
            )
            deduped_count += 1
            # Skip this relationship - the alternate path will handle connectivity
        else:
            final_rels.append(rel)

    # Fourth pass: canonicalize names
    for rel in final_rels:
        from_col = rel.from_columns[0] if rel.from_columns else ""
        to_col = rel.to_columns[0] if rel.to_columns else ""
        canonical_name = generate_relationship_name(
            rel.from_dataset,
            from_col,
            rel.to_dataset,
            to_col,
        )
        if rel.unique_name != canonical_name:
            renamed_count += 1
            rel.unique_name = canonical_name

    model.relationships = final_rels

    if renamed_count or deduped_count:
        logger.info(
            "Normalized final relationships: kept=%s renamed=%s removed_duplicates=%s",
            len(model.relationships),
            renamed_count,
            deduped_count,
        )

    for rel in model.relationships:
        logger.info(
            "FINAL REL: %s (%s.%s -> %s.%s)",
            rel.unique_name,
            rel.from_dataset,
            rel.from_column,
            rel.to_dataset,
            rel.to_column,
        )

def _has_path(self, from_table: str, to_table: str, graph: dict[str, set[str]], exclude_edge: tuple[str, str] = None, max_depth: int = 5) -> bool:
    """Check if there's a path from from_table to to_table, optionally excluding a specific edge.

    Uses BFS with max depth to detect if alternate paths exist through intermediary tables.
    Limited to 5 hops to avoid exploring distant relationships.
    """
    from collections import deque

    if from_table == to_table:
        return True

    queue: deque = deque([(from_table, 0)])
    visited: set[str] = {from_table}

    while queue:
        current, depth = queue.popleft()

        if depth >= max_depth:
            continue

        for next_table in graph.get(current, set()):
            # Skip excluded edge
            if exclude_edge and (current, next_table) == exclude_edge:
                continue

            if next_table == to_table:
                return True

            if next_table not in visited:
                visited.add(next_table)
                queue.append((next_table, depth + 1))

    return False

def _select_strongest_relationship(self, candidates: list[SMLRelationship]) -> SMLRelationship:
    """Select the strongest relationship from ambiguous candidates.

    Strength hierarchy:
    1. Explicit foreign keys (from metadata)
    2. FK column naming pattern (ends with _id, _key, etc.)
    3. Column name matches target table name
    4. First encountered (fallback)
    """
    # Score each candidate
    scored = []
    for rel in candidates:
        score = 0
        from_col = rel.from_columns[0] if rel.from_columns else ""
        to_dataset = rel.to_dataset.upper()

        # Preference 1: Explicit foreign key (marked in metadata)
        if getattr(rel, "is_explicit_fk", False):
            score += 100

        # Preference 2: Standard FK naming conventions
        fk_suffixes = ["_ID", "_KEY", "_FK", "_CODE"]
        if any(from_col.upper().endswith(suffix) for suffix in fk_suffixes):
            score += 50

        # Preference 3: Column matches target table name
        # e.g., scenario_id -> scenario table
        col_base = from_col.upper().rstrip("_ID_KEYFK")
        if col_base == to_dataset or col_base.rstrip("S") == to_dataset:
            score += 25

        scored.append((score, rel))

    # Return highest-scored relationship, or first if tied
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]
