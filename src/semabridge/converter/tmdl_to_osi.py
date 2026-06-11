import logging
import re
from typing import Any

from semabridge.core.interfaces import BaseConverter
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIDimension,
    OSIAttribute,
    OSIRelationship,
    OSIDataType,
    OSICardinality,
    OSICrossFilterDirection,
)
from semabridge.utils.relationship_naming import generate_relationship_name

logger = logging.getLogger(__name__)


class TMDLToOSIConverter(BaseConverter):
    """
    Transforms Fabric TMDL text files into OSI Model.
    """

    SYSTEM_TABLE_PREFIXES = (
        "LocalDateTable_",
        "DateTableTemplate_",
    )

    def to_osi(self, source_data: dict[str, Any]) -> OSIModel:
        tmdl_files = source_data.get("tmdl", {})
        if not tmdl_files:
            tmdl_files = source_data  # fallback if passed directly
            
        dataset_id = source_data.get("dataset_id", "unknown_model")
        display_name = source_data.get("display_name", dataset_id)

        model = OSIModel(
            unique_name=display_name,
            label=display_name,
            source_platform="fabric",
            metadata={"workspace_id": source_data.get("workspace_id"), "dataset_id": dataset_id},
        )
        
        for path, content in tmdl_files.items():
            if not isinstance(content, str):
                continue
            normalized_path = str(path).replace("\\", "/").lower()
            if "/tables/" in f"/{normalized_path}" and normalized_path.endswith(".tmdl"):
                self._parse_table_tmdl(path, content, model)
            elif "relationships" in path:
                self._parse_relationships_tmdl(path, content, model)

        self._finalize_table_roles(model)
        logger.info(
            "Parsed TMDL model '%s': %d user table(s), %d measure(s), %d relationship(s)",
            model.unique_name,
            len([ds for ds in model.datasets if not ds.is_hidden]),
            len([metric for metric in model.metrics if not metric.is_hidden]),
            len(model.relationships),
        )
        return model

    def from_osi(self, osi_model: OSIModel) -> Any:
        """
        Convert OSIModel to TMDL.

        Not implemented in this unidirectional converter.
        """
        raise NotImplementedError("TMDLToOSIConverter only supports TMDL -> OSI conversion.")

    def _parse_table_tmdl(self, path: str, content: str, model: OSIModel) -> None:
        lines = content.split('\n')
        
        current_table = None
        current_column = None
        current_metric = None
        current_object = None
        
        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue
                
            # table Fact
            table_match = re.match(r'^table\s+([\'"]?)(.*?)\1\s*$', line_stripped)
            if table_match:
                table_name = table_match.group(2)
                if self._is_system_table(table_name):
                    logger.info("Skipping hidden auto-date TMDL table: %s", table_name)
                    current_table = None
                    current_column = None
                    current_metric = None
                    current_object = None
                    break
                current_table = OSIDataset(unique_name=table_name)
                model.datasets.append(current_table)
                current_column = None
                current_metric = None
                current_object = "table"
                continue
                
            if not current_table:
                continue

            if line_stripped == "isHidden" or line_stripped.startswith("isHidden:"):
                is_hidden = self._parse_tmdl_bool(line_stripped, default=True)
                if current_object == "column" and current_column:
                    current_column.is_hidden = is_hidden
                elif current_object == "measure" and current_metric:
                    current_metric.is_hidden = is_hidden
                continue
                
            # column Amount
            col_match = re.match(r'^column\s+([\'"]?)(.*?)\1\s*$', line_stripped)
            if col_match:
                col_name = col_match.group(2)
                current_column = OSIColumn(unique_name=col_name)
                current_table.columns.append(current_column)
                current_metric = None
                current_object = "column"
                continue
                
            # measure Actual = CALCULATE(...)
            measure_match = re.match(r'^measure\s+([\'"]?)(.*?)\1\s*=\s*(.*)$', line_stripped)
            if measure_match:
                measure_name = measure_match.group(2)
                expression = measure_match.group(3)
                
                metric = OSIMetric(
                    unique_name=measure_name,
                    label=measure_name,
                    dataset=current_table.unique_name,
                    expression=expression,
                )
                model.metrics.append(metric)
                current_column = None
                current_metric = metric
                current_object = "measure"
                continue
                
            if current_column and line_stripped.startswith("dataType:"):
                dt_str = line_stripped.split(":", 1)[1].strip().lower()
                current_column.data_type = self._map_tmdl_data_type(dt_str)
                continue

        if current_table:
            self._append_dimension_for_table(model, current_table)

    def _parse_relationships_tmdl(self, path: str, content: str, model: OSIModel) -> None:
        current_name = None
        from_column = None
        to_column = None

        def flush_relationship() -> None:
            nonlocal current_name, from_column, to_column
            if not (current_name and from_column and to_column):
                return

            from_dataset, from_col = self._split_table_column(from_column)
            to_dataset, to_col = self._split_table_column(to_column)
            if not (from_dataset and from_col and to_dataset and to_col):
                return

            model.relationships.append(
                OSIRelationship(
                    unique_name=generate_relationship_name(
                        from_dataset,
                        from_col,
                        to_dataset,
                        to_col,
                    ),
                    from_dataset=from_dataset,
                    from_columns=[from_col],
                    to_dataset=to_dataset,
                    to_columns=[to_col],
                    cardinality=OSICardinality.MANY_TO_ONE,
                    cross_filter_direction=OSICrossFilterDirection.SINGLE,
                    is_active=True,
                )
            )

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            rel_match = re.match(r"^relationship\s+(.+)$", line)
            if rel_match:
                flush_relationship()
                current_name = rel_match.group(1).strip().strip("'\"")
                from_column = None
                to_column = None
                continue

            if line.startswith("fromColumn:"):
                from_column = line.split(":", 1)[1].strip()
            elif line.startswith("toColumn:"):
                to_column = line.split(":", 1)[1].strip()

        flush_relationship()

    def _is_system_table(self, table_name: str) -> bool:
        return any(str(table_name).startswith(prefix) for prefix in self.SYSTEM_TABLE_PREFIXES)

    def _parse_tmdl_bool(self, line: str, default: bool = False) -> bool:
        if ":" not in line:
            return default
        value = line.split(":", 1)[1].strip().lower()
        if value in {"false", "0", "no"}:
            return False
        if value in {"true", "1", "yes"}:
            return True
        return default

    def _map_tmdl_data_type(self, data_type: str) -> OSIDataType:
        normalized = re.sub(r"[^a-z0-9]", "", data_type.lower())
        if normalized in {"int64", "integer", "int", "whole"}:
            return OSIDataType.INTEGER
        if normalized in {"decimal", "double", "currency"}:
            return OSIDataType.DECIMAL
        if normalized in {"single", "float"}:
            return OSIDataType.FLOAT
        if normalized in {"boolean", "bool"}:
            return OSIDataType.BOOLEAN
        if normalized in {"date", "datetime"}:
            return OSIDataType.DATE if normalized == "date" else OSIDataType.DATETIME
        if normalized in {"time"}:
            return OSIDataType.TIME
        if normalized in {"string", "text"}:
            return OSIDataType.STRING
        return OSIDataType.STRING

    def _append_dimension_for_table(self, model: OSIModel, dataset: OSIDataset) -> None:
        attributes = [
            OSIAttribute(
                unique_name=column.unique_name,
                label=column.label,
                dataset=dataset.unique_name,
                source_column=column.unique_name,
                is_hidden=column.is_hidden,
            )
            for column in dataset.columns
            if not column.is_hidden
        ]
        if not attributes:
            return
        model.dimensions.append(
            OSIDimension(
                unique_name=dataset.unique_name,
                label=dataset.label,
                dataset=dataset.unique_name,
                attributes=attributes,
                is_hidden=dataset.is_hidden,
            )
        )

    def _finalize_table_roles(self, model: OSIModel) -> None:
        valid_dataset_names = {dataset.unique_name for dataset in model.datasets}
        model.relationships = [
            relationship
            for relationship in model.relationships
            if relationship.from_dataset in valid_dataset_names
            and relationship.to_dataset in valid_dataset_names
        ]

        visible_datasets = [ds for ds in model.datasets if not ds.is_hidden]
        metric_counts: dict[str, int] = {}
        for metric in model.metrics:
            metric_counts[metric.dataset] = metric_counts.get(metric.dataset, 0) + 1

        for dataset in visible_datasets:
            name_upper = dataset.unique_name.upper()
            dataset.is_fact = (
                name_upper == "FACT"
                or name_upper.endswith("FACT")
                or re.search(r"(^|[^A-Z0-9])FACT([^A-Z0-9]|$)", name_upper) is not None
                or re.search(r"(^|[^A-Z0-9])TRANSACTION(S)?([^A-Z0-9]|$)", name_upper) is not None
            )

        if any(ds.is_fact for ds in visible_datasets):
            return

        if metric_counts:
            fact_name = max(metric_counts, key=metric_counts.get)
            for dataset in visible_datasets:
                dataset.is_fact = dataset.unique_name == fact_name

    def _split_table_column(self, value: str) -> tuple[str | None, str | None]:
        cleaned = str(value).strip().strip("'\"")
        if "." not in cleaned:
            return None, None
        table_name, column_name = cleaned.split(".", 1)
        return table_name.strip().strip("'\""), column_name.strip().strip("'\"")
