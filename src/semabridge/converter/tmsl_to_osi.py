"""
TMSL to OSI Converter.

Converts Fabric Model Definitions (TMSL JSON) into the OSI (Open Semantic Interchange)
canonical intermediate representation.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from semabridge.core.interfaces import BaseConverter
from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIDimension,
    OSIAttribute,
    OSIHierarchy,
    OSILevel,
    OSIDataType,
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class TMSLToOSIConverter(BaseConverter):
    """
    Transforms Fabric TMSL JSON into OSI Model.
    """

    def to_osi(self, source_data: Dict[str, Any]) -> OSIModel:
        """
        Convert TMSL dictionary to OSIModel object.

        Args:
            source_data: Dictionary containing:
                - tmsl: Decoded model.bim JSON
                - workspace_id: Fabric workspace ID
                - dataset_id: Fabric dataset ID (unique_name for OSIModel)

        Returns:
            OSIModel object
        
        Raises:
            ConversionError: If transformation fails.
        """
        try:
            tmsl_json = source_data.get("tmsl", {})
            workspace_id = source_data.get("workspace_id")
            dataset_id = source_data.get("dataset_id")

            if not tmsl_json or not dataset_id:
                raise ConversionError(
                    "Missing 'tmsl' or 'dataset_id' in source_data",
                    source_format="tmsl",
                    target_format="osi"
                )

            model_obj = tmsl_json.get("model", {})
            name = model_obj.get("name", "FabricModel")

            osi_model = OSIModel(
                unique_name=dataset_id,
                label=name,
                description=model_obj.get("description", ""),
                source_platform="fabric",
                metadata={"workspace_id": workspace_id}
            )

            # Process Datasets (Tables)
            if "tables" in model_obj:
                for table in model_obj["tables"]:
                    # Skip internal tables
                    t_name = table.get("name", "")
                    if t_name.startswith("DateTableTemplate") or t_name.startswith("LocalDateTable"):
                        continue
                    
                    dataset = self._parse_dataset(table)
                    osi_model.datasets.append(dataset)

                    # Create corresponding Dimension for each Dataset
                    # In TMSL/Power BI, every table is potentially a dimension
                    dim = self._create_dimension_from_dataset(dataset)
                    if dim:
                        osi_model.dimensions.append(dim)

                    # Process Measures (Metrics)
                    if "measures" in table:
                        for measure in table["measures"]:
                            metric = self._parse_metric(measure, dataset.unique_name)
                            osi_model.metrics.append(metric)

            # Process Relationships
            # Build set of valid dataset names (excluding filtered-out tables like LocalDateTable_*)
            valid_datasets = {ds.unique_name for ds in osi_model.datasets}
            
            if "relationships" in model_obj:
                for rel in model_obj["relationships"]:
                    osi_rel = self._parse_relationship(rel)
                    if osi_rel:
                        # Only add relationship if both referenced tables exist
                        if (osi_rel.from_dataset in valid_datasets and 
                            osi_rel.to_dataset in valid_datasets):
                            osi_model.relationships.append(osi_rel)
                        else:
                            # Skip relationships to excluded tables (e.g., LocalDateTable_*)
                            logger.debug(
                                f"Skipping relationship '{osi_rel.unique_name}': "
                                f"references excluded table(s) "
                                f"({osi_rel.from_dataset} -> {osi_rel.to_dataset})"
                            )

            return osi_model

        except Exception as e:
            logger.error(f"TMSL to OSI conversion failed: {e}")
            raise ConversionError(
                f"Failed to convert TMSL: {e}",
                source_format="tmsl",
                target_format="osi",
                details={"error": str(e)}
            )

    def from_osi(self, osi_model: OSIModel) -> Any:
        # Implementing simple TMSL generation or raising NotImplemented
        # Ideally, we should use a separate OSIToTMSLConverter or TmslGenerator
        raise NotImplementedError("OSI to TMSL conversion is handled by TmslGenerator")

    def _create_dimension_from_dataset(self, dataset: OSIDataset) -> Optional[OSIDimension]:
        """Create an implicit dimension from a dataset."""
        if dataset.is_hidden:
            return None
            
        attributes = []
        for col in dataset.columns:
            # Skip hidden columns or potential measures (metrics usually come separately, but columns might be hidden)
            if col.is_hidden:
                continue
                
            attr = OSIAttribute(
                unique_name=col.unique_name,
                label=col.label,
                dataset=dataset.unique_name,
                source_column=col.unique_name,
                is_hidden=col.is_hidden
            )
            attributes.append(attr)
            
        if not attributes:
            return None
            
        return OSIDimension(
            unique_name=dataset.unique_name,
            label=dataset.label,
            description=dataset.description,
            dataset=dataset.unique_name,
            attributes=attributes,
            is_hidden=dataset.is_hidden
        )

    def _parse_dataset(self, table_def: Dict[str, Any]) -> OSIDataset:
        """Parse a TMSL table into OSIDataset."""
        name = table_def["name"]
        
        columns = []
        if "columns" in table_def:
            for col in table_def["columns"]:
                columns.append(self._parse_column(col))
        
        # Source table logic
        source_table = name
        if name == "Table":
            source_table = "DEVICE_INVENTORY"  # Legacy heuristic from PRD/Test environment

        return OSIDataset(
            unique_name=name,
            label=name,
            description=table_def.get("description"),
            is_hidden=table_def.get("isHidden", False),
            columns=columns,
            source_table=source_table
        )

    def _parse_column(self, col_def: Dict[str, Any]) -> OSIColumn:
        """Parse a TMSL column into OSIColumn with Cortex AI metadata."""
        tmsl_type = col_def.get("dataType", "string")
        col_name = col_def.get("name", "")

        type_map = {
            "int64": OSIDataType.INTEGER,
            "double": OSIDataType.FLOAT,
            "decimal": OSIDataType.DECIMAL,
            "boolean": OSIDataType.BOOLEAN,
            "dateTime": OSIDataType.DATETIME,
            "string": OSIDataType.STRING,
            "binary": OSIDataType.BINARY
        }

        mapped_type = type_map.get(tmsl_type, OSIDataType.STRING)

        # Determine if key (heuristic on name pattern)
        is_key = False
        upper_name = col_name.upper()
        if upper_name.endswith("ID") or upper_name.endswith("KEY") or upper_name.startswith("PK_") or upper_name.startswith("FK_"):
            is_key = True

        # ── Cortex AI metadata ──────────────────────────────────────────────
        # Auto-generate synonyms from snake_case / PascalCase column names
        synonyms = self._auto_synonyms(col_name)

        # is_enum heuristic: TMSL dataCategory == "Category" or boolean type
        is_enum = (
            col_def.get("dataCategory", "").lower() == "category"
            or mapped_type == OSIDataType.BOOLEAN
        )

        # Fabric may emit calculated column expressions as a list of lines.
        # OSIColumn.source_expression expects a string.
        source_expr = col_def.get("sourceColumn") or col_def.get("expression") or ""
        if isinstance(source_expr, list):
            source_expr = "\n".join(str(x) for x in source_expr)
        elif source_expr is None:
            source_expr = ""
        else:
            source_expr = str(source_expr)

        return OSIColumn(
            unique_name=col_name,
            label=col_name,
            data_type=mapped_type,
            description=col_def.get("description"),
            is_hidden=col_def.get("isHidden", False),
            format_string=col_def.get("formatString"),
            is_key=is_key,
            source_expression=source_expr,
            synonyms=synonyms,
            is_enum=is_enum,
        )

    @staticmethod
    def _auto_synonyms(name: str) -> List[str]:
        """
        Generate simple synonym candidates from a column/measure name.

        Example: "CustomerID" → ["Customer ID", "Client ID"]
                 "sale_amount" → ["Sale Amount", "Sales Amount"]
        """
        # Convert snake_case and PascalCase to title-case words
        # e.g. "sale_amount" → "Sale Amount"
        snake_separated = name.replace("_", " ").strip()
        # Insert space before uppercase letters following lowercase (PascalCase)
        import re as _re
        camel_separated = _re.sub(r"(?<=[a-z])(?=[A-Z])", " ", snake_separated)
        title_form = camel_separated.title().strip()

        synonyms: List[str] = []
        if title_form and title_form.lower() != name.lower():
            synonyms.append(title_form)

        # Common business abbreviation expansions
        _abbrev_map = {
            "Cust": "Customer", "Acct": "Account", "Amt": "Amount",
            "Qty": "Quantity", "Num": "Number", "Id": "ID",
            "Desc": "Description", "Dt": "Date", "Yr": "Year",
            "Mth": "Month", "Qtr": "Quarter", "Wk": "Week",
        }
        for abbrev, expansion in _abbrev_map.items():
            if abbrev in title_form:
                synonyms.append(title_form.replace(abbrev, expansion))

        # Return deduplicated list (up to 3 synonyms)
        seen: List[str] = []
        for s in synonyms:
            if s not in seen and s.lower() != name.lower():
                seen.append(s)
        return seen[:3]

    def _parse_metric(self, measure_def: Dict[str, Any], dataset_name: str) -> OSIMetric:
        """Parse a TMSL measure into OSIMetric with Cortex AI metadata."""
        dax = measure_def.get("expression", "")
        if isinstance(dax, list):
            dax = "\n".join(dax)

        name = measure_def["name"]

        # access_modifier heuristic:
        # - Names prefixed with "_" or ending with " Helper" / " Base" → private_access
        # - Hidden measures → private_access
        # - All others → public_access
        is_helper = (
            name.startswith("_")
            or any(name.lower().endswith(suffix) for suffix in (" helper", " base", " temp", " internal"))
            or measure_def.get("isHidden", False)
        )
        access_modifier = "private_access" if is_helper else "public_access"

        # Auto-generate synonyms from measure name
        synonyms = TMSLToOSIConverter._auto_synonyms(name)

        return OSIMetric(
            unique_name=name,
            label=name,
            dataset=dataset_name,
            expression=dax,
            aggregation=OSIAggregationType.NONE,  # Raw DAX implies explicit calc
            description=measure_def.get("description"),
            format_string=measure_def.get("formatString"),
            is_hidden=measure_def.get("isHidden", False),
            access_modifier=access_modifier,
            synonyms=synonyms,
        )

    def _parse_relationship(self, rel_def: Dict[str, Any]) -> Optional[OSIRelationship]:
        """Parse TMSL relationship."""
        try:
             name = rel_def.get("name", f"Rel_{rel_def['fromTable']}_{rel_def['toTable']}")
             
             card_map = {
                 "manytoone": OSICardinality.MANY_TO_ONE,
                 "onetoone": OSICardinality.ONE_TO_ONE,
                 "onetomany": OSICardinality.ONE_TO_MANY,
                 "manytomany": OSICardinality.MANY_TO_MANY
             }
             raw_card = rel_def.get("cardinality", "ManyToOne").lower()
             
             cf_map = {
                 "single": OSICrossFilterDirection.SINGLE,
                 "both": OSICrossFilterDirection.BOTH
             }
             raw_cf = rel_def.get("crossFilteringBehavior", "Single").lower()

             return OSIRelationship(
                 unique_name=name,
                 from_dataset=rel_def["fromTable"],
                 from_columns=[rel_def["fromColumn"]],
                 to_dataset=rel_def["toTable"],
                 to_columns=[rel_def["toColumn"]],
                 cardinality=card_map.get(raw_card, OSICardinality.MANY_TO_ONE),
                 cross_filter_direction=cf_map.get(raw_cf, OSICrossFilterDirection.SINGLE),
                 is_active=rel_def.get("isActive", True)
             )
        except Exception as e:
            logger.warning(f"Failed to parse relationship '{rel_def.get('name')}': {e}")
            return None
