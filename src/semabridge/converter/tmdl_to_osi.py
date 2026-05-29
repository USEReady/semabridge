from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from semabridge.core.interfaces import BaseConverter
from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import (
    OSIModel,
    OSIRelationship,
    OSICardinality,
    OSICrossFilterDirection,
    RelationshipCardinality,
    RelationshipCrossFiltering,
)
from semabridge.utils.logger import get_logger
from semabridge.converter.tmdl_parser import TMDLParser
from semabridge.converter.osi_builder import OSIBuilder

logger = get_logger(__name__)

SYSTEM_TABLE_PATTERNS = [
    r"^DateTableTemplate_",
    r"^LocalDateTable_",
    r".*Template.*",
]


def _table_header(content: str) -> str:
    """Return the table header section before the first structural child."""
    header_end = len(content)
    first_marker = re.search(r"^\s*(column|measure|partition|hierarchy|change|lineageTag)\b", content, re.MULTILINE)
    if first_marker:
        header_end = first_marker.start()
    return content[:header_end]


def _table_is_hidden(content: str) -> bool:
    header = _table_header(content)
    return bool(re.search(r"^\s*isHidden\b", header, re.MULTILINE))

def is_system_table(
    table_name: str,
    content: str = ""
) -> bool:
    """
    Detect Power BI auto-generated system tables.
    """
    return any(
        re.search(pattern, table_name)
        for pattern in SYSTEM_TABLE_PATTERNS
    ) or (
        bool(content)
        and (
            "__PBI_TemplateDateTable" in content
            or "__PBI_LocalDateTable" in content
            or "showAsVariationsOnly" in _table_header(content)
        )
    )


def analyze_tmdl_tables(tmdl_files: Dict[str, str]) -> List[Dict[str, Any]]:
    """Classify TMDL tables for diagnostics and deployability decisions."""
    table_files = {
        path: content
        for path, content in tmdl_files.items()
        if path.startswith("definition/tables/") and path.endswith(".tmdl")
    }

    relationship_counts: Dict[str, int] = {}
    for content in table_files.values():
        for rel in TMDLParser.parse_relationships(content):
            from_table = str(rel.get("fromTable") or "").casefold()
            to_table = str(rel.get("toTable") or "").casefold()
            if from_table:
                relationship_counts[from_table] = relationship_counts.get(from_table, 0) + 1
            if to_table:
                relationship_counts[to_table] = relationship_counts.get(to_table, 0) + 1

    table_records: List[Dict[str, Any]] = []
    for path, content in table_files.items():
        table_name = TMDLParser.derive_table_name(path, content)
        hidden = _table_is_hidden(content)
        system = is_system_table(table_name, content)
        measure_count = len(TMDLParser.parse_measures(content))
        relationship_count = relationship_counts.get(table_name.casefold(), 0)
        hidden_fact = hidden and not system and (relationship_count > 0 or measure_count > 0)
        included = (not system) and (not hidden or hidden_fact)

        table_records.append(
            {
                "path": path,
                "name": table_name,
                "is_hidden": hidden,
                "is_system": system,
                "measure_count": measure_count,
                "relationship_count": relationship_count,
                "is_hidden_fact": hidden_fact,
                "include": included,
            }
        )

    return table_records


def get_all_user_tables(tmdl_files: Dict[str, str]) -> List[str]:
    """
    Get all valid user table names, excluding system/template tables and isolated hidden helpers.
    """
    return [record["name"] for record in analyze_tmdl_tables(tmdl_files) if record["include"]]


class TMDLToOSIConverter(BaseConverter):
    """
    Transforms Microsoft Fabric TMDL files into an OSI canonical intermediate representation.
    """

    def to_osi(self, source_data: Dict[str, Any]) -> OSIModel:
        """
        Convert TMDL files dictionary to an OSIModel object.

        Args:
            source_data: Dictionary containing:
                - tmdl_files: Dict[str, str] mapping path to content
                - workspace_id: Fabric workspace ID
                - dataset_id: Fabric dataset ID (unique_name for OSIModel)
                - display_name: display name of dataset

        Returns:
            OSIModel object
        
        Raises:
            ConversionError: If transformation fails.
        """
        try:
            tmdl_files = source_data.get("tmdl_files", {})
            workspace_id = source_data.get("workspace_id")
            dataset_id = source_data.get("dataset_id")
            display_name = source_data.get("display_name") or dataset_id or "FabricModel"

            if not tmdl_files or not dataset_id:
                raise ConversionError(
                    "Missing 'tmdl_files' or 'dataset_id' in source_data",
                    source_format="tmdl",
                    target_format="osi"
                )

            osi_model = OSIModel(
                unique_name=display_name,
                label=display_name,
                description="",
                source_platform="fabric",
                metadata={"workspace_id": workspace_id, "dataset_id": dataset_id}
            )

            # 1. Parse tables directly from definition/tables/*.tmdl
            table_files = {
                path: content
                for path, content in tmdl_files.items()
                if path.startswith("definition/tables/") and path.endswith(".tmdl")
            }

            user_table_names = get_all_user_tables(tmdl_files)
            user_tables_set = set(user_table_names)

            user_tables_count = 0
            system_tables_count = 0
            total_measures_count = 0

            for path, content in table_files.items():
                # Derive table name
                table_name = TMDLParser.derive_table_name(path, content)

                record = next((item for item in analyze_tmdl_tables(tmdl_files) if item["name"] == table_name), None)
                if not record or not record["include"]:
                    system_tables_count += 1
                    if record and record["is_hidden_fact"]:
                        logger.info("Including hidden fact table: %s", table_name)
                    elif record and record["is_hidden"] and not record["is_system"]:
                        logger.info("Excluding isolated hidden helper table: %s", table_name)
                    elif record and record["is_system"]:
                        logger.info("Excluding system table: %s", table_name)
                    continue

                user_tables_count += 1

                # Parse raw columns and measures
                parsed = TMDLParser.parse_table_file(content)
                total_measures_count += len(parsed["measures"])

                # Build dataset and add to model
                dataset = OSIBuilder.build_dataset(table_name, parsed["columns"])
                osi_model.datasets.append(dataset)

                # Create corresponding Dimension for each Dataset
                dim = OSIBuilder.build_dimension_from_dataset(dataset)
                if dim:
                    osi_model.dimensions.append(dim)

                # Build and append explicit measures
                for raw_measure in parsed["measures"]:
                    metric = OSIBuilder.build_metric(raw_measure, dataset.unique_name)
                    osi_model.metrics.append(metric)

                # Create summarizeBy aggregation columns as metrics
                for agg_metric in OSIBuilder.build_metrics_from_aggregation_columns(dataset):
                    osi_model.metrics.append(agg_metric)

            # 2. Parse Relationships across all files
            relationships = self._parse_relationships_tmdl(tmdl_files, osi_model)
            osi_model.relationships.extend(relationships)

            # Mark datasets starting with FACT or similar patterns as fact tables
            for ds in osi_model.datasets:
                if ds.unique_name.upper().startswith(("FACT", "FCT_", "F_")):
                    ds.is_fact = True

            # Logging structural counts exactly
            logger.info(f"Detected {user_tables_count} user tables: {', '.join(sorted(user_table_names))}")
            logger.info(f"Filtered {system_tables_count} system tables")
            logger.info(f"Detected {total_measures_count} measures")
            logger.info(f"Detected {len(osi_model.relationships)} relationships")

            return osi_model

        except Exception as e:
            logger.error(f"TMDL to OSI conversion failed: {e}")
            raise ConversionError(
                f"Failed to convert TMDL: {e}",
                source_format="tmdl",
                target_format="osi",
                details={"error": str(e)}
            )

    def from_osi(self, osi_model: OSIModel) -> Any:
        raise NotImplementedError("OSI to TMDL conversion is not supported")

    def _parse_relationships_tmdl(self, tmdl_files: Dict[str, str], osi_model: OSIModel) -> List[OSIRelationship]:
        """Convert native TMDL relationships into OSIRelationship objects."""
        relationships = []
        parsed_raw = []
        for path, content in tmdl_files.items():
            raw_rels = TMDLParser.parse_relationships(content)
            for raw_rel in raw_rels:
                parsed_raw.append(raw_rel)

        # Build case-insensitive map of valid datasets
        valid_dataset_map = {
            str(ds.unique_name).casefold(): ds.unique_name for ds in osi_model.datasets
        }

        # Map relationships to correct datasets or skip missing ones
        for raw_rel in parsed_raw:
            if str(raw_rel.get("joinOnDateBehavior")).strip().casefold() == "datepartonly":
                logger.info("Skipping date variation relationship: %s", raw_rel.get("name"))
                continue

            from_dataset = valid_dataset_map.get(str(raw_rel["fromTable"]).casefold())
            to_dataset = valid_dataset_map.get(str(raw_rel["toTable"]).casefold())
            if from_dataset and to_dataset:
                # Update tables with correct casing
                raw_rel["fromTable"] = from_dataset
                raw_rel["toTable"] = to_dataset

                # Build relationship
                osi_rel = OSIBuilder.build_relationship(raw_rel)

                # Set extra properties requested
                osi_rel.relationship_id = raw_rel.get("name")
                
                # Parse cardinality and cross filtering behavior
                card_enum = self._infer_cardinality(raw_rel.get("cardinality"))
                # Map to standard OSICardinality
                osi_rel.cardinality = OSICardinality[card_enum.name]
                
                filter_behavior = self._parse_cross_filtering(raw_rel.get("crossFiltering"))
                if filter_behavior == RelationshipCrossFiltering.bothDirections:
                    osi_rel.cross_filter_direction = OSICrossFilterDirection.BOTH
                else:
                    osi_rel.cross_filter_direction = OSICrossFilterDirection.SINGLE
                
                osi_rel.join_on_date_behavior = raw_rel.get("joinOnDateBehavior")

                relationships.append(osi_rel)
                
                from_col = osi_rel.from_columns[0] if osi_rel.from_columns else ""
                to_col = osi_rel.to_columns[0] if osi_rel.to_columns else ""
                logger.info(f"Including relationship: {osi_rel.from_dataset}.{from_col} → {osi_rel.to_dataset}.{to_col}")
            else:
                logger.debug(
                    f"Skipping relationship '{raw_rel.get('name')}': "
                    f"references unavailable table(s) "
                    f"({raw_rel.get('fromTable')} -> {raw_rel.get('toTable')})"
                )
        
        logger.info(f"Parsed {len(relationships)} relationships from TMDL")
        return relationships

    def _infer_cardinality(self, card_str: Optional[str]) -> RelationshipCardinality:
        """Infer relationship cardinality with MANY_TO_ONE fallback."""
        if not card_str:
            return RelationshipCardinality.MANY_TO_ONE
        norm = card_str.strip().lower()
        if "manytoone" in norm or "many_to_one" in norm:
            return RelationshipCardinality.MANY_TO_ONE
        if "onetomany" in norm or "one_to_many" in norm:
            return RelationshipCardinality.ONE_TO_MANY
        if "onetoone" in norm or "one_to_one" in norm:
            return RelationshipCardinality.ONE_TO_ONE
        if "manytomany" in norm or "many_to_many" in norm:
            return RelationshipCardinality.MANY_TO_MANY
        return RelationshipCardinality.MANY_TO_ONE

    def _parse_cross_filtering(self, filter_str: Optional[str]) -> RelationshipCrossFiltering:
        """Parse cross filtering behavior with oneDirection fallback."""
        if not filter_str:
            return RelationshipCrossFiltering.oneDirection
        norm = filter_str.strip().lower()
        if "both" in norm:
            return RelationshipCrossFiltering.bothDirections
        if "automatic" in norm:
            return RelationshipCrossFiltering.automatic
        return RelationshipCrossFiltering.oneDirection
