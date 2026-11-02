"""Fabric TMDL (Tabular Model Definition Language) Reverse Emitter.

Enables 'Govern in Snowflake, Push Back to BI' by translating canonical OSI models
and Snowflake Semantic Views back into Microsoft Fabric TMDL format.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.intermediate.models import (
    OSIAggregationType,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)

logger = logging.getLogger("semabridge.connectors.tmdl_emitter")

# Map OSI types to TMDL dataType values
_OSI_TO_TMDL_TYPE: Dict[OSIDataType, str] = {
    OSIDataType.STRING: "string",
    OSIDataType.INTEGER: "int64",
    OSIDataType.DECIMAL: "decimal",
    OSIDataType.FLOAT: "double",
    OSIDataType.BOOLEAN: "boolean",
    OSIDataType.DATE: "dateTime",
    OSIDataType.DATETIME: "dateTime",
}


class FabricTmdlEmitter:
    """Emits Microsoft Fabric Tabular Model Definition Language (TMDL) datasets."""

    def __init__(self, compatibility_level: int = 1600, culture: str = "en-US"):
        self.compatibility_level = compatibility_level
        self.culture = culture

    def emit_tmdl_files(self, model: OSIModel, output_dir: str | Path) -> Dict[str, str]:
        """Generate full TMDL folder structure and return mapping of relative file paths to content."""
        out_path = Path(output_dir)
        files: Dict[str, str] = {}

        # 1. model.tmdl
        model_name = model.label or model.unique_name
        model_lines = [
            f"model {model_name}",
            f"\tcompatibilityLevel: {self.compatibility_level}",
            f"\tculture: {self.culture}",
            "",
            "\tannotation PBI_ProTooling = [\"DevMode\"]",
        ]
        files["model.tmdl"] = "\n".join(model_lines)

        # 2. Per-table TMDL files under tables/<TableName>.tmdl
        metrics_by_dataset: Dict[str, List[OSIMetric]] = {}
        for m in model.metrics:
            metrics_by_dataset.setdefault(m.dataset.upper(), []).append(m)

        for ds in model.datasets:
            table_name = ds.label or ds.unique_name
            ds_metrics = metrics_by_dataset.get(ds.unique_name.upper(), [])
            t_lines = [
                f"table '{table_name}'",
                f"\tlineageTag: {ds.unique_name}",
            ]
            if ds.description:
                t_lines.append(f"\tdescription: \"{ds.description}\"")
            t_lines.append("")

            # Columns
            for col in ds.columns:
                c_name = col.label or col.unique_name
                tmdl_type = _OSI_TO_TMDL_TYPE.get(col.data_type, "string")
                t_lines.append(f"\tcolumn '{c_name}'")
                t_lines.append(f"\t\tdataType: {tmdl_type}")
                t_lines.append(f"\t\tsourceColumn: {col.unique_name}")
                t_lines.append(f"\t\tsummarizeBy: none")
                if col.description:
                    t_lines.append(f"\t\tdescription: \"{col.description}\"")
                t_lines.append("")

            # Measures
            for met in ds_metrics:
                m_name = met.label or met.unique_name
                # If DAX expression or SQL is available, use expression
                expr = met.expression or f"SUM('{table_name}'[{met.source_column}])"
                t_lines.append(f"\tmeasure '{m_name}' = {expr}")
                if met.format_string:
                    t_lines.append(f"\t\tformatString: {met.format_string}")
                if met.description:
                    t_lines.append(f"\t\tdescription: \"{met.description}\"")
                t_lines.append("")

            files[f"tables/{table_name}.tmdl"] = "\n".join(t_lines)

        # 3. relationships.tmdl
        if model.relationships:
            rel_lines = []
            for rel in model.relationships:
                from_col = rel.from_columns[0] if rel.from_columns else "ID"
                to_col = rel.to_columns[0] if rel.to_columns else "ID"
                rel_name = rel.unique_name
                rel_lines.append(f"relationship {rel_name}")
                rel_lines.append(f"\tfromColumn: '{rel.from_dataset}'.'{from_col}'")
                rel_lines.append(f"\ttoColumn: '{rel.to_dataset}'.'{to_col}'")
                rel_lines.append("")
            files["relationships.tmdl"] = "\n".join(rel_lines)

        # Write files if output_dir provided
        for rel_file, content in files.items():
            full_path = out_path / rel_file
            full_path.parent.mkdir(parents=True, exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

        return files
