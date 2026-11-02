"""Tableau Data Source (.tds / .twb) Ingestion Connector.

Extracts relations, columns, calculated fields, and joins from Tableau XML
definitions and normalizes them into canonical OSI (Open Semantic Interchange) models.
"""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from semabridge.intermediate.models import (
    OSIAggregationType,
    OSICardinality,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)

logger = logging.getLogger("semabridge.connectors.tableau_connector")

# Map Tableau datatype strings to OSI data types
_TABLEAU_TYPE_MAP: Dict[str, OSIDataType] = {
    "string": OSIDataType.STRING,
    "integer": OSIDataType.INTEGER,
    "real": OSIDataType.FLOAT,
    "boolean": OSIDataType.BOOLEAN,
    "date": OSIDataType.DATE,
    "datetime": OSIDataType.DATETIME,
}


class TableauConnector:
    """Parser and connector for Tableau .tds and .twb files."""

    def __init__(self, file_path: str):
        self.file_path = file_path

    def extract_osi_model(self, model_name: Optional[str] = None) -> OSIModel:
        """Parse Tableau XML content and build an OSI model."""
        tree = ET.parse(self.file_path)
        root = tree.getroot()

        # Handle both standalone .tds (<datasource>) and full workbook .twb (<workbook><datasources><datasource>)
        datasource = root if root.tag == "datasource" else root.find(".//datasource")
        if datasource is None:
            raise ValueError(f"No valid <datasource> element found in {self.file_path}")

        extracted_name = model_name or datasource.get("name") or datasource.get("caption") or "TABLEAU_MODEL"
        connection = datasource.find(".//connection")

        datasets_map: Dict[str, OSIDataset] = {}
        metrics: List[OSIMetric] = []
        relationships: List[OSIRelationship] = []

        # 1. Discover Tables / Relations
        relations = datasource.findall(".//relation")
        for rel in relations:
            rel_type = rel.get("type")
            table_name = rel.get("table") or rel.get("name")
            if not table_name:
                continue
            # Strip brackets: "[orders]" -> "orders"
            clean_name = table_name.strip("[]")
            if clean_name not in datasets_map:
                datasets_map[clean_name] = OSIDataset(
                    unique_name=clean_name.upper(),
                    label=clean_name,
                    source_table=clean_name,
                    columns=[],
                )

        # Fallback dataset if no explicit relations
        if not datasets_map:
            default_ds_name = "DEFAULT_DATASET"
            datasets_map[default_ds_name] = OSIDataset(
                unique_name=default_ds_name,
                label="Default Dataset",
                source_table="TABLEAU_DATA",
                columns=[],
            )

        primary_dataset = next(iter(datasets_map.values()))

        # 2. Extract Columns and Calculated Fields
        for col in datasource.findall(".//column"):
            name = col.get("name", "").strip("[]")
            caption = col.get("caption") or name
            datatype_str = col.get("datatype", "string")
            role = col.get("role", "dimension")
            calc = col.find("calculation")

            osi_type = _TABLEAU_TYPE_MAP.get(datatype_str.lower(), OSIDataType.STRING)

            if calc is not None:
                # Calculated field / measure
                formula = calc.get("formula", "")
                agg_type = OSIAggregationType.SUM
                upper_f = formula.upper()
                if upper_f.startswith("COUNT"):
                    agg_type = OSIAggregationType.COUNT
                elif upper_f.startswith("AVG"):
                    agg_type = OSIAggregationType.AVG
                elif upper_f.startswith("MIN"):
                    agg_type = OSIAggregationType.MIN
                elif upper_f.startswith("MAX"):
                    agg_type = OSIAggregationType.MAX

                metrics.append(
                    OSIMetric(
                        unique_name=name.upper(),
                        label=caption,
                        dataset=primary_dataset.unique_name,
                        expression=formula,
                        aggregation=agg_type,
                        description=f"Tableau calculation: {caption}",
                        synonyms=[caption] if caption != name else [],
                    )
                )
            else:
                # Regular dimension or attribute column
                primary_dataset.columns.append(
                    OSIColumn(
                        unique_name=name.upper(),
                        label=caption,
                        data_type=osi_type,
                        is_key=(name.lower().endswith("id") or name.lower().endswith("key")),
                        synonyms=[caption] if caption != name else [],
                    )
                )

        return OSIModel(
            unique_name=extracted_name.upper().replace(" ", "_"),
            label=extracted_name,
            source_platform="Tableau",
            datasets=list(datasets_map.values()),
            metrics=metrics,
            relationships=relationships,
        )
