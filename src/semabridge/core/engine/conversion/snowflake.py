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

def _convert_snowflake_to_sml(self, context: RunContext) -> SMLModel:
    """Convert Snowflake source to SML."""
    from semabridge.connectors.inference_engine import SmlInferenceEngine
    from semabridge.connectors.measure_detector import MeasureDetector
    from semabridge.connectors.relationship_detector import RelationshipDetector
    from semabridge.sml.assembler import SMLAssembler

    config = context.config
    sf = context.source_format

    # Prefer semantic-view driven conversion when Step 4 captured DDL.
    if sf.semantic_view_ddl:
        try:
            from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
            from semabridge.converter.osi_to_sml import OSIToSMLConverter

            column_metadata = {
                table_name: [col.model_dump() for col in cols]
                for table_name, cols in sf.columns.items()
            }
            osi_model = SemanticViewToOSIConverter().to_osi(
                {
                    "ddl": sf.semantic_view_ddl,
                    "view_name": sf.semantic_view_name or context.project_id,
                    "column_metadata": column_metadata,
                }
            )
            context.osi_model = osi_model
            sml_model = OSIToSMLConverter().from_osi(osi_model)

            self._record_step(
                6,
                StepStatus.SUCCESS,
                f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics",
            )
            return sml_model
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Semantic-view conversion failed for '%s'; falling back to metadata inference: %s",
                sf.semantic_view_name or context.project_id,
                exc,
            )

    # Convert source format back to metadata dict for existing assembler
    metadata = {
        "database": sf.database,
        "schema": sf.schema_name,
        "tables": {name: {"description": t.description, "row_count": t.row_count}
                  for name, t in sf.tables.items()},
        "columns": {name: [c.model_dump() for c in cols]
                   for name, cols in sf.columns.items()},
        "foreign_keys": [fk.model_dump() for fk in sf.foreign_keys],
        "primary_keys": sf.primary_keys,
    }

    assembler = SMLAssembler(
        model_name=context.project_id,
        description=config.model.description,
        source_database=sf.database,
        source_schema=sf.schema_name,
    )

    # Add tables (deduplicate columns by name to avoid downstream conflicts)
    for table_name, table_info in metadata["tables"].items():
        columns = metadata["columns"].get(table_name, [])
        seen_cols: set[str] = set()
        deduped_cols: list[dict[str, Any]] = []
        for col in columns:
            col_name = str(col.get("name") or col.get("COLUMN_NAME") or "").strip()
            if not col_name:
                continue
            key = col_name.upper()
            if key in seen_cols:
                logger.debug("Skipping duplicate column %s in table %s", col_name, table_name)
                continue
            seen_cols.add(key)
            deduped_cols.append(col)
        assembler.add_table(
            table_name=table_name,
            columns=deduped_cols,
            description=table_info.get("description", ""),
            row_count=table_info.get("row_count"),
        )

    # Detect relationships
    rel_detector = RelationshipDetector(
        tables=metadata["tables"],
        columns=metadata["columns"],
        primary_keys=metadata["primary_keys"],
        explicit_fks=metadata["foreign_keys"],
    )
    relationships = rel_detector.detect_all()

    for rel in relationships:
        from_table = str(
            rel.get("from_table")
            or rel.get("source_table")
            or rel.get("left_table")
            or ""
        ).strip()
        from_column = str(
            rel.get("from_column")
            or rel.get("source_column")
            or rel.get("left_column")
            or ""
        ).strip()
        to_table = str(
            rel.get("to_table")
            or rel.get("target_table")
            or rel.get("right_table")
            or ""
        ).strip()
        to_column = str(
            rel.get("to_column")
            or rel.get("target_column")
            or rel.get("right_column")
            or ""
        ).strip()
        rel_name = str(rel.get("name") or "").strip() or generate_relationship_name(
            from_table,
            from_column,
            to_table,
            to_column,
        )

        if not (from_table and from_column and to_table and to_column):
            logger.warning("Skipping malformed relationship during SML conversion: %s", rel)
            continue

        assembler.add_relationship(
            name=rel_name,
            from_table=from_table,
            from_column=from_column,
            to_table=to_table,
            to_column=to_column,
        )

    # Classify tables
    engine = SmlInferenceEngine(
        tables=metadata["tables"],
        columns=metadata["columns"],
        relationships=relationships,
        primary_keys=metadata["primary_keys"],
    )
    scores = engine.classify()

    classification_map = {}
    for ds in assembler._datasets:
        score = scores.get(ds.unique_name)
        if score:
            classification_map[ds.unique_name] = score.classification
            ds.is_fact = score.classification == "FACT"

    # Detect measures
    measure_detector = MeasureDetector(
        tables=metadata["tables"],
        columns=metadata["columns"],
        relationships=relationships,
    )
    all_measures = measure_detector.detect_all_measures(classification=classification_map)

    for table_name, measures in all_measures.items():
        for measure in measures[:5]:
            assembler.add_metric(
                name=measure["name"],
                dataset=table_name,
                source_column=measure["column"],
                aggregation=measure["aggregation"],
            )

    sml_model = assembler.build()

    # Populate context.osi_model via SML→OSI roundtrip for auditing (mandatory OSI pass)
    try:
        from semabridge.converter.sml_to_osi import SMLToOSIConverter
        context.osi_model = SMLToOSIConverter().to_osi(sml_model)
    except Exception as e:
        logger.warning(f"OSI roundtrip for Snowflake source failed (non-fatal): {e}")

    self._record_step(
        6, StepStatus.SUCCESS,
        f"{sml_model.dataset_count} datasets, {sml_model.metric_count} metrics"
    )

    return sml_model
